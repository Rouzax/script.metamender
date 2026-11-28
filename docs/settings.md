# Settings

Open the settings with **Add-ons > My add-ons > Program add-ons > MetaMender >
Configure**. These are the settings that apply to the whole add-on. What each
library gets (which fields to fill, which rating to show, the age rating
country, and where plots and titles come from) is chosen per library in
MetaMender's library list; see [Libraries](libraries.md).

Some settings only appear when you raise the settings level (the cog at the
bottom left of the settings screen) to **Advanced**; they are marked
*(Advanced)* below. Changes apply from the next scan.

## Providers

**Manual and help**
:   Opens a window with a QR code and the address of this manual
    (`rouzax.github.io/script.metamender/docs/`), to open on your phone or
    computer. It is the first entry of the settings, so when you open them
    the cursor already rests on it: press Left once to reach the list of
    tabs. Nothing is changed or saved; **Close** (or Back) returns you to
    Kodi.

### TMDB

**TMDB API key**
:   Your own free key from themoviedb.org; see
    [setup](setup.md#1-tmdb-api-key). Without a working key MetaMender
    updates nothing and tells you so.

### fanart.tv

**Personal fanart.tv API key**
:   Optional. MetaMender asks fanart.tv for art with its own key; with
    your own personal key as well, new images reach you sooner. fanart.tv
    gives you a personal key with an account on its website. MetaMender works
    without it, and it only matters for libraries with **Posters**,
    **Fanart** or **Other art** ticked (see [Art](libraries.md#art)). If
    fanart.tv rejects the key, art that needs fanart.tv waits, with nothing
    changed, until it works
    again; everything else carries on. A notification tells you when your
    personal key is refused (once for background scans, each time for a
    manual scan or Update now): check it here, or clear the field to use
    MetaMender's own key.

## Backfill

### Freshness

**Staleness threshold (days)**
:   From what age a movie or episode counts as settled. Content older than
    this is checked once; newer content is checked again on the refresh
    schedule, and one last time when it reaches this age. Default: 365.
    See [how often a field is checked](scans.md#how-often-a-field-is-checked).

**Refresh schedule (days)** *(Advanced)*
:   The gaps between the checks of new content, separated by commas. The
    default `7,30,90` checks again about a week after the first check, then
    about a month after that, then about three months after that.

### Sources

**Run setup wizard**
:   Walks through the setup again, starting from your current answers. See
    [Setup](setup.md#changing-your-answers-later).

**Libraries**
:   Opens the library list, where each library gets its own choices. See
    [Libraries](libraries.md).

## Certifications

### Country scrapers

These apply to every library that fills age ratings. Which country's rating
system a library uses is chosen on that library's page; see
[Age ratings](age-ratings.md).

**Enable FSK lookup**
:   Ask the German FSK when TMDB has no rating. Its ratings count directly for
    Germany and are converted for the other countries. Default: on.

**Enable BBFC lookup**
:   Ask the British BBFC when TMDB has no rating. Its ratings count directly
    for the United Kingdom and are converted for the other countries.
    Default: on.

**Enable Medieraadet lookup**
:   Ask the Danish Media Council when TMDB has no rating. It rates films only.
    Its ratings count directly for Denmark and are converted for the other
    countries. Default: on.

## General

### Schedule

**Scan interval (hours)**
:   Hours between background scans. Default: 24. A shorter interval does not
    check fields more often than their schedule says; it only notices new
    items and due fields sooner, at the cost of more requests.

**API rate limit (seconds)** *(Advanced)*
:   The least time between two requests to a site. Raise it if a site starts
    refusing requests. Default: 0.25.

### Notifications

**Show notifications**
:   A short notification after a background scan that changed something.
    Windows for problems that need you are always shown. Default: on.

**Resume interrupted scans**
:   A scan interrupted by a Kodi restart continues where it stopped, instead of
    starting over. Default: on.

### Appearance

**Window colours**
:   The colours of MetaMender's windows: **Golden Hour** (default),
    **Ultraviolet**, **Ember** or **Nightfall**. The add-on icon and
    notifications stay amber. See the [examples](details.md#colours).

### Maintenance

**Orphan retention (days)**
:   After a full scan, MetaMender forgets items that have not been in your
    library for this many days, such as deleted movies, including any locks
    and kept original values they had. 0 never forgets them. Default: 30.

**Reset tracker** *(Advanced)*
:   Forgets when each field was checked, so every field is checked again at
    the next scan. Locked fields and kept original values stay. Use it after
    a wrong setting or to force a full refresh; the next scan then takes as
    long as the first one.

### Debugging

**Enable debug logging** *(Advanced)*
:   Writes a detailed log file for every scan to the add-on's data folder
    (`addon_data/script.metamender/logs/`, the last 7 days, at most 30
    scans). Switch it on when you report a problem. Default: off.
