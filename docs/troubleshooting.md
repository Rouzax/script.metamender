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

Art has stricter rules: local image files that exist, art from other
websites and art that changed outside MetaMender are never replaced. See
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
check everything again. A movie MetaMender moved or added to a set has no
Restore: take it out with Kodi's **Remove from set**, or put it in another
set, and MetaMender leaves it there.

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

### Art did not change

This covers posters, fanart, the other art types (clear logo, key art,
landscape, banner, disc art, clear art, character art) and episode thumbs.

- **Its tick is off for the library**: **Posters**, **Fanart**, **Other
  art** and **Episode thumbs** are all off until you tick them.
- **The art is a local image file** that exists, for example one Artwork Dump saved, or
  art from a website other than TMDB or fanart.tv. MetaMender leaves
  those alone; the Details window says *Local image file, left as it is* or
  *Image from another source, left as it is*.
- **An episode thumb from a website that MetaMender did not set**, such as
  a TMDB still your scraper chose, is kept: only frame grabs, broken links
  and empty thumbs are replaced.
- **The art changed outside MetaMender**, so it is locked. Choose **Let
  MetaMender update this again** in the Details window to unlock it.
- **The current image is still as good**: art is only replaced when a
  better image clearly leads, and one without votes or likes never replaces
  one that has them.
- **Neither site has that art type** for the item. Landscape, banner, disc
  art, clear art and character art come from fanart.tv only, so a movie or
  show fanart.tv does not know keeps what it has. Disc art is only managed
  for movies and character art only for shows; the extra fanart
  (`fanart1` and so on) is never changed.
- **A season of a show without a TMDB id** is left alone, and a show without
  a TheTVDB id gets no art from fanart.tv.
- **fanart.tv did not answer** or rejected the key: art that needs it waits
  until it does. If it rejected the key, a notification said so (once for
  background scans, each time for a manual scan or Update now): *fanart.tv
  refused your personal API key* means the key in Settings, Providers is
  wrong (fix it, or clear it to use MetaMender's own key); *fanart.tv refused
  MetaMender's key* is not something you can fix, and art resumes once
  fanart.tv accepts the key again. See
  [Personal fanart.tv API key](settings.md#fanarttv).
- **The item has an NFO file**: its art is only filled when empty, a frame
  grab or a broken link.
- **The art is settled.** Like other fields, art is checked while the title
  is new and then left alone. **Update now** checks it once more.

### Art not filled after deleting files

You deleted local art files so MetaMender would fill the art, but Kodi still
shows no image.

- **It has not been checked yet.** Background scans check whether local art
  files still exist once a week. Open MetaMender from **Add-ons > Program
  add-ons > MetaMender** to run a manual scan, which checks them all at
  once, or use **Update now** on the item.
- **Its tick is off for the library.** Only ticked art types are checked:
  **Posters**, **Fanart**, **Other art** and **Episode thumbs**. A locked
  field is never checked.
- **The share was offline at the check.** MetaMender never treats an
  unreachable drive or share as a deleted file; it checks again at the next
  scan.
- **The video file itself is gone**, for example an older release you
  replaced that Kodi still lists. MetaMender leaves such an item alone until
  you clean the library (**Clean library** in Kodi's video settings or
  menu). See
  [If you delete your local art files](libraries.md#if-you-delete-your-local-art-files).
- **You deleted the whole art folder, or emptied it**, and it was not inside
  the video's folder. An empty or missing folder looks the same as a network
  share that is not mounted, so MetaMender leaves the art alone. Recreate the
  folder with any file in it and run a manual scan.
- **Neither site has that art** for the item, so the slot stays empty. See
  [If you delete your local art files](libraries.md#if-you-delete-your-local-art-files).
- **It is extra fanart** (`fanart1` and so on), which is never filled.

### A movie set was left alone

A set keeps its name, plot and art when:

- **Movie sets is not ticked** for the library the set follows. A set
  follows the library most of its movies are in; see
  [Which library a set follows](libraries.md#which-library-a-set-follows).
- **Two libraries hold equally many of its movies**, so MetaMender cannot
  tell which language to use. Movies in a subfolder with its own language
  count as a library of their own.
- **None of its movies is in an included library** or folder.
- **Its movies do not share one TMDB collection**: no collection holds more
  than half of them, as with a set you made by hand. A movie TMDB knows no
  collection for counts against every collection.
- **TMDB did not answer** when MetaMender asked for a movie's collection.
  That movie then counts for no collection in that scan, which can leave
  the set alone; MetaMender asks again at the next scan.
- **It is a duplicate** of a set MetaMender renamed: MetaMender moves its
  movies into the renamed set and leaves the duplicate itself as it is.
  Kodi removes it, once empty, when you clean the library. See
  [Duplicate sets](libraries.md#duplicate-sets).
- **It shares a collection with the set MetaMender manages** but has a name
  of its own, such as "Favourites". MetaMender does not touch it or move
  movies out of it. If no set of a collection carries a name of that
  collection, none of them is managed.
- **Every one of its movies has an NFO file**: its name, plot and art are
  only filled when empty.
- **Its name changed outside MetaMender**, so the name is locked. The
  Details window can unlock it.
- **One of its movies could not be reached and every other movie has an
  NFO file**, so MetaMender cannot tell whether the set counts as having
  NFO files; it checks again at the next scan. One movie without an NFO
  file that can be reached settles this, so the set is not held back. If
  the share is online, the unreachable movie is likely an older release you
  replaced whose file is gone: clean the library (**Clean library** in
  Kodi's video settings or menu) so Kodi drops it.
- **It is settled.** Like other fields, a set is checked while its newest
  movie is new and then left alone. **Update now** checks it once more.

### A movie was not added to its set

MetaMender adds a movie in no set to its collection's set at the next scan
(see [Movies in no set](libraries.md#movies-in-no-set)). It stays out when:

- **Movie sets is not ticked** for the movie's library or folder, or the
  library or folder is left out.
- **TMDB lists it in no collection.** MetaMender asks again once that answer
  is 180 days old.
- **You took it out of the set**, or put it in another set, after MetaMender
  added or moved it. MetaMender respects that for good.
- **The collection's set is one MetaMender leaves alone**, such as a set of
  your own that holds movies of several collections; the Details window of
  that set says why (see
  [A movie set was left alone](#a-movie-set-was-left-alone)). MetaMender
  does not create a second set beside it.
- **TMDB did not answer**, or Kodi could mistake the set's name for another
  set's. MetaMender tries again at the next scan.
- **You used Update now on the movie.** That does not add it to a set; the
  next scan does.

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
