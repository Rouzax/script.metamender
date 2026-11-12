# Install

MetaMender is a Kodi add-on. You install it once; after a short setup it
runs in the background on its own.

## What you need

- **Kodi 21 (Omega) or Kodi 22 (Piers)**, on any platform.
- **A video library in Kodi.** MetaMender improves what your scraper already
  added; it does not add movies or shows itself.
- **A free TMDB API key.** TMDB (The Movie Database) is where most of
  MetaMender's information comes from, and it asks every app for its own key.
  The [setup](setup.md#1-tmdb-api-key) explains how to get one; it takes a few
  minutes and costs nothing.
- **An internet connection** on the device that runs Kodi.

You do not need an account at TheTVDB or IMDb: MetaMender reads TheTVDB with
its own key, and IMDb's ratings come from IMDb's public data.

## Install from the Rouzax repository (recommended)

The repository is a small add-on that lets Kodi install MetaMender and keep it
up to date for you.

1. Download the latest `repository.rouzax-x.y.z.zip` from the
   [Rouzax repository releases](https://github.com/Rouzax/repository.rouzax/releases).
2. In Kodi, open **Settings > Add-ons > Install from zip file** and choose the
   zip you downloaded.
3. Open **Install from repository > Rouzax Repository > Program add-ons >
   MetaMender** and choose **Install**.

Kodi installs the add-ons MetaMender needs along with it, including the
**IMDb Ratings** module from the official Kodi repository.

!!! note "Install from zip file is greyed out?"
    Kodi only installs zip files when **Settings > System > Add-ons > Unknown
    sources** is switched on. Switch it on for the install; you can switch it
    off again afterwards, and updates from the repository keep working.

!!! info "The official Kodi repository"
    MetaMender is not in the official Kodi repository yet. Once it is, you can
    install it from there without the Rouzax repository.

## Install from a zip file

If you would rather not add a repository, download the latest
`script.metamender-x.y.z.zip` from
[MetaMender's releases](https://github.com/Rouzax/script.metamender/releases/latest)
and install it with **Settings > Add-ons > Install from zip file**. Kodi
fetches the add-ons MetaMender needs from the official Kodi repository. You do
not get updates automatically this way: install a newer zip yourself.

## After installing

Nothing in your library changes yet. MetaMender waits until you have finished
its setup: it needs your TMDB key and the languages of your library before it
can do anything useful.

- Kodi shows a **MetaMender needs you** window that says setup has not been
  finished. Choose **Open setup**.
- Or open MetaMender yourself from **Add-ons > Program add-ons > MetaMender**.

Then follow the [setup](setup.md).

The first time MetaMender needs IMDb ratings, the IMDb Ratings module
downloads IMDb's ratings data. Until that download has finished, IMDb ratings
wait and are filled on a later scan; everything else is updated as normal.
