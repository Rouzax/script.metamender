# Troubleshooting and FAQ

Most questions about a single item are answered fastest by its
[Details window](details.md): open the item's context menu and choose
**MetaMender > Details**. It shows where each value came from and why a field
was left alone.

## Questions you might have

### Will MetaMender overwrite what I or my scraper put there?

Sometimes, on purpose, and never where you told it not to:

- **Fields you did not tick in setup** are never touched.
- **An item with an NFO file** keeps its values: MetaMender only fills fields
  that are empty (and keeps refreshing what it filled itself).
- **A folder without a language**: MetaMender only fills empty text fields
  there (and keeps refreshing what it filled itself).
- **Everything else** is kept in line with TMDB, TheTVDB and IMDb until the
  field is settled: a value that differs from the site is replaced. That
  includes an edit you made by hand in Kodi, if the field is checked again
  before it is settled.
- **To keep a value as it is**, for example after you corrected it by hand,
  open the item's Details window, select the field and choose **Keep this
  value**. MetaMender then leaves it alone for good.
- **When MetaMender replaced a value**, it kept the old one. **Restore
  original** in the Details window puts it back and locks the field.

NFO files, or switching a field off in setup, protect values from the start.

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

### What does it send over the internet?

Only lookups: an id or a title to find an item at TMDB, TheTVDB or a rating
site, with your TMDB key for TMDB. Nothing about your library as a whole, and
nothing about what you watch, is sent anywhere.

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
- **The field is switched off**: check [What to fill](setup.md#4-what-to-fill).
- **The first scan is still running**: on a large library it takes a while;
  a notification says when it is done.
- **Kodi was playing**: scans wait until playback stops.

### A plot or title is in the wrong language

- **The folder's language is not set** or is wrong: check
  [Folders in another language](setup.md#3-folders-in-another-language). In a
  folder without a language MetaMender keeps existing text.
- **The site has no text in that language yet**: MetaMender keeps the current
  text and checks again on its schedule. The fallback language only fills
  empty fields, so an English plot in a Dutch folder stays until a Dutch one
  appears.
- **Show and movie titles** are only changed with **Show and movie titles**
  ticked.

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
- **Rating source** is set to **TMDB only**.
- **The item has no IMDb id**, or IMDb has no rating for it.

### An age rating looks wrong

- **Wrong country**: check **Country** on the **Certifications** tab;
  **Automatic** follows Kodi's interface language.
- **Wrong prefix for your skin**: use **Override certification prefix**.
- **The American rating is still there**: **Replace incorrect certifications**
  is off, or no rating for your country was found yet.
- **It was converted from another country's rating**: MetaMender replaces it
  when your country's real rating appears. See [Age ratings](age-ratings.md).

### MetaMender changed something I wanted to keep

Open the Details window, select the field and choose **Restore original**. The
old value comes back and the field is locked. See
[Restore original and the lock](details.md#restore-original-and-the-lock).

### "A scan is already running"

Only one scan runs at a time. Wait until the background scan has finished,
then try **Update now** again.

## Reporting a problem

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
