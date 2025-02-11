import numpy as np

#fixed_alpha = -2.7
fixed_alpha = -3.0

def powerlaw_model(r, F, alpha=fixed_alpha):
    return F * r**alpha

def subtract_one_real(X):
    tim, refs, fluxes, gpixscale = X

    halo = np.zeros(tim.shape, np.float32)
    for ref,flux in zip(refs,fluxes):
        H,W = tim.shape
        ok,x,y = tim.subwcs.radec2pixelxy(ref.ra, ref.dec)
        x -= 1.
        y -= 1.
        pixscale = tim.imobj.pixscale
        pixrad = int(np.ceil(ref.radius * 3600. / pixscale))
        xlo = int(np.clip(np.floor(x - pixrad), 0, W-1))
        xhi = int(np.clip(np.ceil (x + pixrad), 0, W-1))
        ylo = int(np.clip(np.floor(y - pixrad), 0, H-1))
        yhi = int(np.clip(np.ceil (y + pixrad), 0, H-1))
        if xlo == xhi or ylo == yhi:
            continue

        rads = np.hypot(np.arange(ylo, yhi+1)[:,np.newaxis] - y,
                        np.arange(xlo, xhi+1)[np.newaxis,:] - x)
        maxr = pixrad
        # Apodization fraction
        apr = maxr*0.8
        apodize = np.clip((rads - maxr) / (apr - maxr), 0., 1.)
        # The power-law scale was computed at the coadd pixscale, which might
        # differ from this tim's pixscale; scale it.
        h = powerlaw_model(rads * pixscale / gpixscale, flux)
        # ASSUME tim is in nanomaggies units
        halo[ylo:yhi+1, xlo:xhi+1] += h
    return halo

def subtract_one(X):
    try:
        return subtract_one_real(X)
    except:
        import traceback
        traceback.print_exc()
        raise

def subtract_halos(tims, refs, fluxes2, pixscale, bands, plots, ps, mp):
    iband = dict([(b,i) for i,b in enumerate(bands)])
    args = [(tim, refs, fluxes2[:,iband[tim.band]], pixscale) for tim in tims]
    haloimgs = mp.map(subtract_one, args)
    for tim,h in zip(tims, haloimgs):

        if plots:
            import pylab as plt
            plt.clf()
            plt.subplot(1,3,1)
            plt.imshow(tim.data, origin='lower', interpolation='nearest',
                       vmin=-2*tim.sig1, vmax=5.*tim.sig1)
            plt.colorbar(orientation='horizontal')
            plt.subplot(1,3,2)
            plt.imshow(h, origin='lower', interpolation='nearest',
                       vmin=-2*tim.sig1, vmax=5.*tim.sig1)
            plt.colorbar(orientation='horizontal')
            plt.subplot(1,3,3)
            plt.imshow(tim.data-h, origin='lower', interpolation='nearest',
                       vmin=-2*tim.sig1, vmax=5.*tim.sig1)
            plt.colorbar(orientation='horizontal')
            plt.suptitle(tim.name)
            ps.savefig()

        tim.data -= h

def fit_halos(coimgs, cons, H, W, targetwcs, pixscale,
              bands, gaia, plots, ps, init_fluxes=None):
    from scipy.optimize import minimize

    haloimgs = [np.zeros((H,W),np.float32) for b in bands]
    fitvalues = []

    for istar,g in enumerate(gaia):
        print('Star w/ G=', g.phot_g_mean_mag)
        ok,x,y = targetwcs.radec2pixelxy(g.ra, g.dec)
        x -= 1.
        y -= 1.
        ix = int(np.round(x))
        iy = int(np.round(y))

        pixrad = int(np.ceil(g.radius * 3600. / pixscale))
        print('Pixel radius:', pixrad)

        # Radial profile bins
        radii = np.arange(15, pixrad, 5)
        minr = int(radii[0])
        maxr = int(radii[-1])
        # Apodization fraction
        apr = maxr*0.8

        # segment percentile to fit
        segpct = 10

        ylo,yhi = max(0,iy-maxr), min(H,iy+maxr+1)
        xlo,xhi = max(0,ix-maxr), min(W,ix+maxr+1)
        if yhi-ylo <= 1 or xhi-xlo <= 1:
            # no overlap
            fitvalues.append(None)
            continue

        r2 = ((np.arange(ylo, yhi)[:,np.newaxis] - y)**2 +
              (np.arange(xlo, xhi)[np.newaxis,:] - x)**2)
        rads = np.sqrt(r2)
        #
        apodize = np.clip((rads - maxr) / (apr - maxr), 0., 1.)

        # Pre-compute power-law profile shape
        powerlaw_shape = powerlaw_model(rads, 1.)

        if plots:
            segpros = []
            profiles = []
            fitpros = []
            rimgs = []
            fitdata = []

        fit_fluxes = []

        for iband,band in enumerate(bands):
            rimg = (coimgs[iband][ylo:yhi, xlo:xhi] -
                    haloimgs[iband][ylo:yhi, xlo:xhi])

            if init_fluxes is not None:
                # Add back in the initial guess
                rimg += init_fluxes[istar][iband] * powerlaw_shape

            if plots:
                rimgs.append(rimg)
                fitpro = np.zeros_like(rimg)
                rpro = np.zeros_like(rimg)
                rsegpro = np.zeros_like(rimg)
                segpros.append(rsegpro)
                fitpros.append(fitpro)
                profiles.append(rpro)

            Nseg = 12
            segments = (Nseg * (np.arctan2(np.arange(ylo,yhi)[:,np.newaxis]-y,
                                           np.arange(xlo,xhi)[np.newaxis,:]-x) - -np.pi) / (2.*np.pi)).astype(int)

            rr = []
            mm = []
            dm = []
            # Radial bins
            for rlo,rhi in zip(radii, radii[1:]):
                IY,IX = np.nonzero((r2 >= rlo**2) * (r2 < rhi**2))
                ie = cons[iband][IY+ylo, IX+xlo]
                rseg = []
                # Azimuthal segments
                for s in range(Nseg):
                    K = (ie > 0) * (segments[IY,IX] == s)
                    if np.sum(K):
                        rm = np.median(rimg[IY[K],IX[K]])
                        rseg.append(rm)
                        if plots:
                            rsegpro[IY[K],IX[K]] = rm
                rseg = np.array(rseg)
                rseg = rseg[np.isfinite(rseg)]
                if len(rseg):
                    lo,quart,med = np.percentile(rseg, [segpct, 25, 50])
                    rr.append((rlo+rhi)/2.)
                    mm.append(lo)
                    dm.append(((med-quart)/2.))
                    if plots:
                        rpro[IY,IX] = lo

            rr = np.array(rr)
            mm = np.array(mm)
            dm = np.array(dm)
            dm = np.maximum(dm, 0.1*mm)

            def powerlaw_lnp(r, f, df, F):
                mod = powerlaw_model(r, F)
                return np.sum(((f - mod) / df)**2)

            def powerlaw_obj(X):
                (F,) = X
                return powerlaw_lnp(rr, mm, dm, F)

            M = minimize(powerlaw_obj, [1.])
            (F,) = M.x

            print('Fit flux:', band, '=', F)
            if F < 0:
                print('Setting flux to zero')
                F = 0

            #mod = powerlaw_model(rads, F)
            mod = powerlaw_shape * F
            K = (r2 >= minr**2) * (r2 <= maxr**2)
            if plots:
                fitpro[K] += mod[K]
                fitdata.append((band, rr, mm, dm, F))
            haloimgs[iband][ylo:yhi, xlo:xhi] += K * mod * apodize
            fit_fluxes.append(F)
        fitvalues.append((fit_fluxes, fixed_alpha, minr, maxr, apr))

        if plots:
            import pylab as plt
            plt.clf()
            for (band, rr, mm, dm, F) in fitdata:
                cc = dict(z='m').get(band,band)
                plt.loglog(rr, mm, '-', color=cc)
                plt.errorbar(rr, mm, yerr=dm, color=cc, fmt='.')
                plt.plot(rr, powerlaw_model(rr, F), '-', color=cc, lw=3, alpha=0.3)
            ps.savefig()

        if False and plots:
            plt.clf()
            dimshow(get_rgb(rimgs, bands, **rgbkwargs))
            plt.title('rimg')
            ps.savefig()

            plt.clf()
            dimshow(get_rgb(segpros, bands, **rgbkwargs))
            plt.title('rseg')
            ps.savefig()

            plt.clf()
            dimshow(get_rgb(profiles, bands, **rgbkwargs))
            plt.title('rpro')
            ps.savefig()

            plt.clf()
            dimshow(get_rgb(fitpros, bands, **rgbkwargs))
            plt.title('r fit')
            ps.savefig()

            plt.clf()
            dimshow(get_rgb([co[ylo:yhi,xlo:xhi] - halo[ylo:yhi,xlo:xhi] for co,halo in zip(coimgs,haloimgs)], bands, **rgbkwargs))
            plt.title('data - r fit (fixed)')
            ps.savefig()

    return fitvalues,haloimgs
