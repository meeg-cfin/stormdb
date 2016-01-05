"""
=========================
Methods to process data in StormDB layout

Credits:
    Several functions are modified versions from those in mne-python
    https://github.com/mne-tools/mne-python
=========================

"""
# Author: Chris Bailey <cjb@cfin.au.dk>
#
# License: BSD (3-clause)
import os
import sys
import logging
import numpy as np
from scipy import optimize, linalg

from mne.io import Raw
from mne.io.constants import FIFF

from .access import DBError


class Maxfilter():
    """ Object for maxfiltering data from database into StormDB filesystem

    Parameters
    ----------
    proj_code : str
        The name of the project.
    verbose : bool
        If True, print out a bunch of information as we go. Defaults to False.

    Attributes
    ----------
    proj_code : str
        Name of project
    cmd : str
        If defined, represents a maxfilter shell call as a string.
    """

    def __init__(self, proj_code, verbose=False):
        if not os.path.exists('/projects/' + proj_code):
            raise DBError('No such project!')

        self.proj_code = proj_code
        self.cmd = ''  # No command defined at init
        # Consider placing other vars here

        self.logger = logging.getLogger('__name__')
        self.logger.propagate=False
        stdout_stream = logging.StreamHandler(sys.stdout)
        self.logger.addHandler(stdout_stream)
        if verbose:
            self.logger.setLevel(logging.INFO)
        else:
            self.logger.setLevel(logging.ERROR)


    @staticmethod
    def fit_sphere_to_headshape(info, ylim=None, zlim=None,
                                verbose=None):
        """ Fit a sphere to the headshape points to determine head center for
            maxfilter. Slightly modified from mne-python.

        Parameters
        ----------
        info : dict
            Measurement info from raw file.

        ylim : tuple (or list) of length 2 (ymin, ymax) in meters
            y-coordinate limits (min and max) on head shape points
            Usefull, e.g., for omitting points on face; ylim = (-np.inf, 0.070)

        zlim : tuple (or list) of length 2 (zmin, zmax) in meters
            z-coordinate limits (min and max) on head shape points

        verbose : bool, str, int, or None
            If not None, override default verbose level.

        Returns
        -------
        radius : float
            Sphere radius in mm.
        origin_head: ndarray
            Head center in head coordinates (mm).
        origin_device: ndarray
            Head center in device coordinates (mm).

        """
        # get head digization points, excluding some frontal points (nose etc.)
        hsp = [p['r'] for p in info['dig']
               if (p['kind'] == FIFF.FIFFV_POINT_EXTRA and not
                   (p['r'][2] < 0 and p['r'][1] > 0))]

        if ylim is not None:
            # self.logger.info("Cutting out points for which "
            #                  "{min:.1f} < y < {max:.1f}".format( \
            #                  min=1e3*ylim[0], max=1e3*ylim[1]))
            hsp = [p for p in hsp if (p[1] > ylim[0] and p[1] < ylim[1])]
        if zlim is not None:
            # self.logger.info("Cutting out points for which "
            #                  "{min:.1f} < y < {max:.1f}".format( \
            #                  min=1e3*zlim[0], max=1e3*zlim[1]))
            hsp = [p for p in hsp if (p[2] > zlim[0] and p[2] < zlim[1])]

        if len(hsp) == 0:
            raise ValueError('No head digitization points found')

        hsp = 1e3 * np.array(hsp)

        # initial guess for center and radius
        xradius = (np.max(hsp[:, 0]) - np.min(hsp[:, 0])) / 2
        yradius = (np.max(hsp[:, 1]) - np.min(hsp[:, 1])) / 2

        radius_init = (xradius + yradius) / 2
        center_init = np.array([0.0, 0.0, np.max(hsp[:, 2]) - radius_init])

        # optimization
        x0 = np.r_[center_init, radius_init]
        cost_fun = lambda x, hsp:\
            np.sum((np.sqrt(np.sum((hsp - x[:3]) ** 2, axis=1)) - x[3]) ** 2)

        disp = True if verbose else False
        x_opt = optimize.fmin_powell(cost_fun, x0, args=(hsp,), disp=disp)

        origin_head = x_opt[:3]
        radius = x_opt[3]

        # compute origin in device coordinates
        trans = info['dev_head_t']
        if (trans['from'] != FIFF.FIFFV_COORD_DEVICE or
            trans['to'] != FIFF.FIFFV_COORD_HEAD):
                raise RuntimeError('device to head transform not found')

        head_to_dev = linalg.inv(trans['trans'])
        origin_device = 1e3 * np.dot(head_to_dev,
                                     np.r_[1e-3 * origin_head, 1.0])[:3]

        return radius, origin_head, origin_device


    def build_maxfilter_cmd(self, in_fname, out_fname, origin='0 0 40', frame='head',
                        bad=None, autobad='off', skip=None, force=False,
                        st=False, st_buflen=16.0, st_corr=0.96, mv_trans=None,
                        movecomp=False, mv_headpos=False, mv_hp=None,
                        mv_hpistep=None, mv_hpisubt=None, hpicons=True,
                        linefreq=None, cal=None, ctc=None, mx_args='',
                        maxfilter_bin='/neuro/bin/util/maxfilter',
                        logfile=None, n_threads=None):

        """ Build a NeuroMag MaxFilter command for later execution.

        Things to implement
        * check that cal-file matches date in infile!
        * check that maxfilter binary is OK

        Parameters
        ----------
        in_fname : string
            Input file name

        out_fname : string
            Output file name

        maxfilter_bin : string
            Full path to the maxfilter-executable

        logfile : string
            Full path to the output logfile

        origin : array-like or string
            Head origin in mm. If None it will be estimated from headshape points.

        frame : string ('device' or 'head')
            Coordinate frame for head center

        bad : string, list (or None)
            List of static bad channels. Can be a list with channel names, or a
            string with channels (names or logical channel numbers)

        autobad : string ('on', 'off', 'n')
            Sets automated bad channel detection on or off

        skip : string or a list of float-tuples (or None)
            Skips raw data sequences, time intervals pairs in sec,
            e.g.: 0 30 120 150

        force : bool
            Ignore program warnings

        st : bool
            Apply the time-domain MaxST extension

        st_buflen : float
            MaxSt buffer length in sec (disabled if st is False)

        st_corr : float
            MaxSt subspace correlation limit (disabled if st is False)

        mv_trans : string (filename or 'default') (or None)
            Transforms the data into the coil definitions of in_fname, or into the
            default frame (None: don't use option)

        movecomp : bool (or 'inter')
            Estimates and compensates head movements in continuous raw data

        mv_headpos : bool
            Estimates and stores head position parameters, but does not compensate
            movements (disabled if mv_comp is False)

        mv_hp : string (or None)
            Stores head position data in an ascii file
            (disabled if mv_comp is False)

        mv_hpistep : float (or None)
            Sets head position update interval in ms (disabled if mv_comp is False)

        mv_hpisubt : string ('amp', 'base', 'off') (or None)
            Subtracts hpi signals: sine amplitudes, amp + baseline, or switch off
            (disabled if mv_comp is False)

        hpicons : bool
            Check initial consistency isotrak vs hpifit
            (disabled if mv_comp is False)

        linefreq : int (50, 60) (or None)
            Sets the basic line interference frequency (50 or 60 Hz)
            (None: do not use line filter)

        cal : string
            Path to calibration file

        ctc : string
            Path to Cross-talk compensation file

        mx_args : string
            Additional command line arguments to pass to MaxFilter

        """


        # determine the head origin if necessary
        if origin is None:
            self.logger.info('Estimating head origin from headshape points..')
            raw = Raw(in_fname)
            r, o_head, o_dev = self.fit_sphere_to_headshape(raw.info, ylim=0.070) # Note: this is not standard MNE...
            raw.close()

            self.logger.info('Fitted sphere: r = {.1f} mm'.format(r))
            self.logger.info('Origin head coordinates: {.1f} {.1f} {.1f} mm'.\
                             format(o_head[0], o_head[1], o_head[2]))
            self.logger.info('Origin head coordinates: {.1f} {.1f} {.1f} mm'.\
                             format(o_dev[0], o_dev[1], o_dev[2]))

            self.logger.info('[done]')
            if frame == 'head':
                origin = o_head
            elif frame == 'device':
                origin = o_dev
            else:
                RuntimeError('invalid frame for origin')

        # format command
        if origin is False:
            cmd = (maxfilter_bin + ' -f {:s} -o {:s} -v '.format(
                  in_fname, out_fname))
        else:
            if not isinstance(origin, str):
                origin = '{:.1f} {:.1f} {:.1f}'.format(origin[0],
                                                       origin[1], origin[2])

            cmd = (maxfilter_bin + \
                  ' -f {:s} -o {:s} -frame {:s} -origin {:s} -v '.format(
                  in_fname, out_fname, frame, origin))

        if bad is not None:
            # format the channels
            if not isinstance(bad, list):
                bad = bad.split()
            bad = map(str, bad)
            bad_logic = [ch[3:] if ch.startswith('MEG') else ch for ch in bad]
            bad_str = ' '.join(bad_logic)

            cmd += '-bad {:s} '.format(bad_str)

        cmd += '-autobad {:s} '.format(autobad)

        if skip is not None:
            if isinstance(skip, list):
                skip = ' '.join(['{:.3f} {:.3f}'.format(s[0], s[1]) for s in skip])
            cmd += '-skip {:s} '.format(skip)

        if force:
            cmd += '-force '

        if st:
            cmd += '-st '
            cmd += ' {:d} '.format(st_buflen)
            cmd += '-corr {:.4f} '.format(st_corr)

        if mv_trans is not None:
            cmd += '-trans {:s} '.format(mv_trans)

        if movecomp:
            cmd += '-movecomp '
            if movecomp == 'inter':
                cmd += ' inter '

            if mv_headpos:
                cmd += '-headpos '

            if mv_hp is not None:
                cmd += '-hp {:s} '.format(mv_hp)

            if mv_hpisubt is not None:
                cmd += 'hpisubt {:s} '.format(mv_hpisubt)

            if hpicons:
                cmd += '-hpicons '

        if linefreq is not None:
            cmd += '-linefreq {:d} '.format(linefreq)

        if cal is not None:
            cmd += '-cal {:s} '.format(cal)

        if ctc is not None:
            cmd += '-ctc {:s} '.format(ctc)

        cmd += mx_args

        if logfile:
            cmd += ' | tee ' + logfile

        self.cmd = cmd

    def submit_to_isis(self, n_jobs=1, fake=False, submit_script=None):
        """ Submit the command built before for processing on the cluster.

        Things to implement
        * check output?

        Parameters
        ----------
        n_jobs : number or None
            Number of parallel threads to allow (Intel MKL). Max 12!
        fake : bool
            If true, run a fake run, just print the command that will be
            submitted.
        submit_script : str or None
            Full path to script handling submission. If None (default),
            the default script is used:
            /usr/local/common/meeg-cfin/configurations/bin/submit_to_isis

        """
        if not self.cmd:
            raise NameError('cmd to submit is not defined yet')

        if n_jobs > 12:
            raise ValueError('isis only has 12 cores!')
        elif n_jobs < 1 or type(n_jobs) is not int:
            raise ValueError('number of jobs must be a positive integer!')

        if submit_script is None:
            submit_script = '\
            /usr/local/common/meeg-cfin/configurations/bin/submit_to_isis'

        if os.system(submit_script + ' 2>&1 > /dev/null') >> 8 == 127:
            raise NameError('submit script ' + submit_script + ' not found')

        self.logger.info('Command to submit:\n{:s}'.format(self.cmd))

        submit_cmd = ' '.join((submit_script,
                               '{:d}'.format(n_jobs), self.cmd))
        if not fake:
            st = os.system(submit_cmd)
            if st != 0:
                raise RuntimeError('qsub returned non-zero '
                                   'exit status {:d}'.format(st))
        else:
            print('Fake run, nothing executed. The command built is:')
            print(submit_cmd)
            self.logger.info('Nothing executed.')
