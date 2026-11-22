# Troubleshooting and FAQ

Most questions about a single item are answered fastest by its
[Details window](details.md): open the item's context menu and choose
**MetaMender > Details**. It shows where each value came from and why a field
was left alone.

## Questions you might have

### Will MetaMender overwrite what I or my scraper put there?

Sometimes, on purpose, and never where you told it not to:

- **Libraries you did not include**, and fields you did not tick for a
  library, are never touched.
- **An item with an NFO file** keeps its values: MetaMender only fills fields
  that are empty (and keeps refreshing what it filled itself).
- **Everything else** is kept in line with TMDB, TheTVDB and IMDb until the
  field is settled: a value that differs from the site is replaced. That
  includes an edit you made by hand in Kodi, if the field is checked again
  before it is settled.
- **To keep a value as it is**, for example after you corrected it by hand,
  open the item's Details window, select the field and choose **Keep this
  value**. MetaMender then leaves it alone for good.
- **When MetaMender replaced a value**, it kept the old one. **Restore
  original** in the Details window puts it back and locks the field.

NFO files, leaving a library out, or switching a field off for a library,
protect values from the start.

Posters and episode thumbs have stricter rules: local image files, posters
from other websites and a poster that changed outside MetaMender are never
replaced. See
[What MetaMender leaves alone](libraries.md#what-metamender-leaves-alone).

### Why did it leave this field alone?

The Details window says why, in one sentence; the
[list of reasons](details.md#why-a-field-was-left-alone) explains each one.

### Where does this value come from, and where can it go?

See [Where your data comes from](where-data-comes-from.md).

### Is it slowing my Kodi down?

MetaMender works in the background and waits a moment between requests to the
sites (**API rate limit (seconds)**). It does not start a scan while a video
plays, and a running scan pauses during playback. The first scan visits every
item and takes the longest; later scans only check fields that are due, which
on most days is a small part of the library.

Kodi reloads the list on screen every time an item in the library changes. So
while you browse your movies or shows, a scan holds its changes: it writes the
next one when you have not pressed anything for a minute, or when you leave
the library (for the home screen, to watch something, or for the settings).
**Update now** is your own request, so it writes straight away.
To end a running scan, choose **Stop scan** in the MetaMender submenu of the
context menu; see [Stopping a scan](scans.md#stopping-a-scan).

### What does it send over the internet?

Only lookups: an id or a title to find an item at TMDB, TheTVDB, fanart.tv
or a rating site, with your TMDB key for TMDB and, if you entered one, your
personal key for fanart.tv. Nothing about your library as a whole, and
nothing about what you watch, is sent anywhere. Kodi itself downloads the
posters and thumbs MetaMender points it at.

### Can I use it with a library shared between several Kodi devices?

Yes, but install MetaMender on **one** device. All devices see its changes
through the shared library. Two devices with MetaMender would each scan and
write the same items.

### How do I undo what MetaMender did?

Per field, with **Restore original** in the Details window. There is no
"undo everything": switching a field off stops further changes but leaves the
values MetaMender already wrote, and **Reset tracker** only makes MetaMender
check everything again.

### How do I remove MetaMender?

Uninstall it under **Add-ons > My add-ons**. The values it wrote stay in your
library.

## Something looks wrong

### Nothing is being updated

- **Setup not finished, no key, or a rejected key**: a **MetaMender needs
  you** window says so when nothing plays. Choose **Open setup**.
- **The library is not included, or the field is switched off for it**:
  check the [library list](libraries.md#the-library-list) and the library's
  [What to fill](libraries.md#what-to-fill). A library you added to Kodi
  after setup stays out until you include it.
- **The item is in no library**: a notice says how many items that applies
  to. See [Items in no library](libraries.md#items-in-no-library).
- **The first scan is still running**: on a large library it takes a while;
  a notification says when it is done.
- **Kodi was playing**: scans wait until playback stops.
- **You were browsing the library**: a scan holds its changes until you have
  not pressed anything for a minute or leave the library.

### A plot or title is in the wrong language

- **The library's language is wrong**: check its
  [Language](libraries.md#language). A library whose items are in several
  languages works best split into one library per language.
- **The site has no text in that language yet**: MetaMender keeps the current
  text and checks again on its schedule. The fallback language only fills
  empty fields, so an English plot in a Dutch library stays until a Dutch one
  appears.
- **Show and movie titles** are only changed with **Show and movie titles**
  ticked for the library. With **Keep the original title**, a movie or show
  gets its title in its own language on purpose; see
  [Titles](libraries.md#titles).

### An episode has no title or plot

- **The sites have nothing yet**: common for new episodes. The Details window
  shows the next check.
- **The episode could not be matched**: open the Details window and select
  **Ids**. "Not matched" means the episode's number points at an episode with
  a different title and date on TMDB or TheTVDB, so MetaMender used nothing
  from that site. This happens when your scraper and the site number a
  season differently; rescraping the show with a scraper that stores ids
  (TMDB or TheTVDB) usually fixes it.
- **Generic titles** such as "Episode 3" are never written.

### IMDb ratings are missing

- **Right after installing**: the IMDb Ratings module first downloads IMDb's
  data. Until that has finished, IMDb ratings wait; they appear after a later
  scan.
- **Which rating to show** is set to **TMDB only** for the library.
- **The item has no IMDb id**, or IMDb has no rating for it.

### An age rating looks wrong

- **Wrong country**: check **Certification country** on the library's
  [page](libraries.md#age-ratings); **Automatic** follows the country of
  the library's language, and Kodi's interface language only when the
  library's language has no country.
- **Wrong prefix for your skin**: change **Rating prefix** on the library's
  page.
- **The American rating is still there**: **Replace other countries'
  ratings** is off for the library, or no rating for your country was found
  yet.
- **It was converted from another country's rating**: MetaMender replaces it
  when your country's real rating appears. See [Age ratings](age-ratings.md).

### A poster or episode thumb did not change

- **Posters** or **Episode thumbs** is not ticked for the library; both are
  off until you tick them.
- **The art is a local image file**, for example one Artwork Dump saved, or
  a poster from a website other than TMDB or fanart.tv. MetaMender leaves
  those alone; the Details window says *Local image file, left as it is* or
  *Image from another source, left as it is*.
- **An episode thumb from a website that MetaMender did not set**, such as
  a TMDB still your scraper chose, is kept: only frame grabs, broken links
  and empty thumbs are replaced.
- **The poster changed outside MetaMender**, so it is locked. Choose **Let
  MetaMender update this again** in the Details window to unlock it.
- **The current poster is still as good**: a poster is only replaced when a
  better one clearly leads, and one without votes or likes never replaces one
  that has them.
- **A season of a show without a TMDB id** is left alone, and a show without
  a TheTVDB id gets no posters from fanart.tv.
- **fanart.tv did not answer** or rejected the key: posters that need it wait
  until it does. If it rejected the key, a notification said so (once for
  background scans, each time for a manual scan or Update now): *fanart.tv
  refused your personal API key* means the key in Settings, Providers is
  wrong (fix it, or clear it to use MetaMender's own key); *fanart.tv refused
  MetaMender's key* is not something you can fix, and posters resume once
  fanart.tv accepts the key again. See
  [Personal fanart.tv API key](settings.md#fanarttv).
- **The item has an NFO file**: its poster and thumb are only filled when
  empty, a frame grab or a broken link.
- **The poster is settled.** Like other fields, a poster is checked while the
  title is new and then left alone. **Update now** checks it once more.

### MetaMender changed something I wanted to keep

Open the Details window, select the field and choose **Restore original**. The
old value comes back and the field is locked. See
[Restore original and the lock](details.md#restore-original-and-the-lock).

### "A scan is already running"

Only one scan runs at a time. Wait until the background scan has finished,
then try **Update now** again.

## Reporting a problem

To read this manual from your phone, choose **Manual and help** at the top of
the add-on settings and scan the QR code.

If something still looks wrong, [open an issue on
GitHub](https://github.com/Rouzax/script.metamender/issues) with:

1. What you expected and what you saw, for one example item.
2. A screenshot of that item's Details window.
3. The log of the scan: switch on **Enable debug logging** (Advanced level,
   **General** tab), let a scan run or use **Update now** on the item, and
   attach the newest file from `addon_data/script.metamender/logs/` in Kodi's
   data folder. Kodi's own log helps too; the
   [Kodi wiki](https://kodi.wiki/view/Log_file) explains where to find it on
   your device.

Look through a log before you post it: it lists the titles and folders of the
items it worked on.

Questions, and anything that is not quite a bug, are welcome in the
[Kodi forum thread](https://forum.kodi.tv/showthread.php?tid=389809).
