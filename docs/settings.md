# Settings

Open the settings with **Add-ons > My add-ons > Program add-ons > MetaMender >
Configure**. The [setup](setup.md) already set the important ones; this page
explains every setting, in the order Kodi shows them.

Some settings only appear when you raise the settings level (the cog at the
bottom left of the settings screen) to **Advanced**; they are marked
*(Advanced)* below. Changes apply from the next scan.

## Providers

### TMDB

**TMDB API key**
:   Your own free key from themoviedb.org; see
    [setup](setup.md#1-tmdb-api-key). Without a working key MetaMender
    updates nothing and tells you so.

### Source priority

**Rating source**
:   Which audience ratings MetaMender keeps up to date, and which one Kodi
    shows. Default: **Follow your scraper**. Setup asks this too, as
    [Which rating to show](setup.md#5-which-rating-to-show).

    - **Follow your scraper** keeps the rating your scraper chose to show for
      each item and refreshes it when it comes from TMDB or IMDb. MetaMender
      keeps both the TMDB and the IMDb rating up to date beside it, but the
      rating Kodi shows never changes source.
    - **Prefer TMDB** or **Prefer IMDb** keeps both up to date and makes the
      preferred one the rating Kodi shows (the other one when the preferred
      has none).
    - **TMDB only** or **IMDb only** keeps only that one up to date and shows
      it.

    An item with an NFO file keeps the shown rating its NFO file chose.

**Text source order**
:   Which site MetaMender asks first for TV show plots and episode titles and
    plots in the folder's language; when the first has nothing, it asks the
    other. Default: **Follow your scraper**, which asks first the site whose
    scraper added the show (TheTVDB or TMDB), so the wording stays the same as
    the rest of the show; TMDB first when that cannot be told. Movies always
    use TMDB. Setup asks this too, as
    [Where plots and titles come from](setup.md#6-where-plots-and-titles-come-from).

**Fallback language order** *(Advanced)*
:   The same choice for text in a folder's
    [fallback language](setup.md#2-library-language). Default: **TheTVDB, then
    TMDB**. Changing it fetches the values that came from the fallback
    language again.

## Backfill

### Fields

The same choices as the [What to fill](setup.md#4-what-to-fill) step of the
setup.

**Enable ratings backfill**
:   Audience ratings and vote counts for movies, shows and episodes. Default:
    on.

**Enable plots backfill**
:   Plots of movies, shows and episodes, in the folder's language. Default: on.

**Enable episode title backfill**
:   Episode titles in the folder's language. Generic titles such as "Episode
    3" count as missing and are never written. Default: off.

**Manage show and movie titles**
:   Replaces a show's or movie's title with its title in the folder's language
    on TMDB (and TheTVDB for shows). Only a title the site really has in that
    language is used; there is no fallback language. Titles from an NFO file
    are kept, and so are titles of items without a TMDB or IMDb id. Default:
    off.

**Enable certifications**
:   Fills and corrects age ratings of movies and shows in the rating system of
    the country on the **Certifications** tab. See [Age ratings](age-ratings.md).
    Default: on.

**Enable credits backfill**
:   Directors and writers. Default: off.

**Enable trailers backfill**
:   Trailer links from TMDB. Default: off.

**Enable taglines backfill**
:   Movie taglines. Default: off.

**Enable details backfill**
:   Genre, studio, country, runtime and first aired date. Default: off.

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

**Folder languages**
:   Opens only the folders step of the setup, to give a folder its own
    language.

## Certifications

### Certification format

**Country**
:   The rating system MetaMender writes. **Automatic** (the default) uses the
    country of Kodi's interface language; age ratings are skipped when that
    country is not in the list. See [Age ratings](age-ratings.md).

**Override certification prefix** *(Advanced)*
:   Write your own prefix instead of the country's usual one (such as `NL:`).
    Use it when your skin expects a particular format. Default: off.

**Custom prefix** *(Advanced)*
:   The prefix to write when the override is on, at most 8 characters, for
    example `de:` or `Rated `. Leave it empty for no prefix.

### Country scrapers

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

### Correction

**Replace incorrect certifications**
:   Replace a rating from another country's system, such as "Rated PG-13" in a
    Dutch library. Ratings already in your country's system are kept, and
    ratings from an NFO file are never replaced. Off: only empty ratings are
    filled. Default: on. Setup asks this too, as the
    [Replace other countries' ratings](setup.md#7-certification-country) tick
    box.

### Fallback

**Apply certification when not found**
:   When no source has a rating for an item after a year of monthly checks,
    write the value below, so you can see the item was checked. Off: such
    items stay empty. Default: off.

**Not-found certification**
:   The value written then, exactly as entered (no country prefix). Common
    values: `NR`, `Not Rated`, `Unrated`. Default: `NR`.

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
    (`addon_data/script.metamender/logs/`, the last 10 scans). Switch it on
    when you report a problem. Default: off.
