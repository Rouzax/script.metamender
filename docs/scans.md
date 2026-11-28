# Scans

After setup MetaMender works on its own, in the background. This page explains
when it looks at your library, how often it checks a field again, and what
you see while it works.

## When MetaMender scans

- **Every day.** A background scan runs once every 24 hours (change this with
  **Scan interval (hours)** in the [settings](settings.md)). The first one
  starts when Kodi starts.
- **After a library update.** When Kodi finishes updating or cleaning the
  video library, MetaMender waits five minutes and then looks at the items
  that are new to it. A scheduled scan that is due at the same time covers
  them anyway. This scan does not move the daily one: that still comes one
  scan interval after the last daily scan.
- **When you ask.** [Update now](update-now.md) refreshes one movie, show,
  season or episode right away.

A scan does not start while a video plays, and a scan that is running pauses
when you start a video and carries on when you stop. If Kodi closes in the
middle of a scan, the next one continues where it left off (switch this off
with **Resume interrupted scans**).

While you browse your movies or shows, a scan holds its changes: Kodi reloads
the list on screen after every change, so MetaMender writes the next one when
you have not pressed anything for a minute or when you leave the library.

### Stopping a scan

While a scan runs, the MetaMender submenu in the context menu of any movie,
show, season or episode has **Stop scan**. The scan stops after the item it is
working on; nothing is undone. The next full scan (the scheduled one, the one
when Kodi starts, or a manual scan) carries on from where this one stopped
(unless **Resume interrupted scans** is off, then it starts from the
beginning). A scan after a library update looks only at the new items and
leaves that point where it is. Update now is not affected.

## How often a field is checked

MetaMender does not fetch everything every day. It remembers, per item and per
field, when it last checked and when the next check is due:

- **The first check** happens at the first scan that sees the item.
- **New movies and episodes** change most: a score has few votes at first, and
  plots and episode titles are often added after release. MetaMender checks
  them again after about a week, then about a month later, then about three
  months later, and once more when the item is a year old.
- **After that the field is settled** and MetaMender leaves it alone. Content
  that is already more than a year old is settled after its first check.

So a score that was 9.4 from 12 votes in an episode's first week gets
refreshed while the votes come in, instead of staying 9.4 forever.

Some exceptions:

- **Age ratings** from a real source (TMDB or the official site for your
  country) are settled after one answer: they do not change. An age rating
  MetaMender had to work out from another country's rating is checked again
  every month for about a year, in case the real one appears.
- **A text from the fallback language**, and a poster in the fallback
  language, without text or in English as a last resort, is checked again
  every month for about a year, so
  a version in your own language replaces it when one appears. A poster in
  your own language replaces it even before anyone has rated that poster.
- **A season's poster** follows the season's age: the air date of its first
  episode.
- **A field MetaMender could not check** (a site did not answer) is tried
  again at the next scan; a failed check never counts as a check.
- **Local art files** are checked about once a week to see whether they
  still exist (a manual scan and Update now check at once); a deleted one is
  filled like empty art. See
  [If you delete your local art files](libraries.md#if-you-delete-your-local-art-files).

The exact schedule is in the settings: **Staleness threshold (days)** (the one year)
and **Refresh schedule (days)** (the week, month and three months; shown at the
Advanced settings level). The [Details window](details.md)
shows, per field, when it was last checked and when it is due next.

### After you refresh an item in Kodi

When you refresh a movie, show or episode in Kodi, or replace its video file
with a new release, Kodi makes a new entry for it with your scraper's values.
At the next scan MetaMender recognises the item and puts back what it had
written there:

- **Text, ratings and art MetaMender wrote** that the scraper replaced are
  written again in that same scan, also for a field that was settled. This
  does not count as a check: each field's next check stays where it was.
- **The scraper's new art is not taken for your choice.** MetaMender treats
  it as the art it first saw, so it is not locked as
  [changed outside MetaMender](libraries.md#what-metamender-leaves-alone).
- **What stays as it is**: a value from an NFO file (on an item with one,
  MetaMender only fills empty fields), a field you locked, and a field
  MetaMender never wrote, which is checked on its usual schedule.

To keep the scraper's text or art instead, open the
[Details window](details.md#restore-original) after that scan and choose
**Restore original** on the field: the scraper's value comes back and the
field is locked.

If a site cannot be reached during that scan, the field gets its value back
when it is next checked. MetaMender recognises a refresh by the new entry
Kodi makes; when Kodi gives the item its old entry number back, which can
happen for the item added last, it looks like an edit made outside
MetaMender. Art locked as changed outside MetaMender by a refresh before
version 1.3.2 stays locked; **Let MetaMender update this again** in the
Details window unlocks it.

## What you see

Nothing, most of the time. MetaMender is quiet on purpose:

- **While anything plays**, video or music, MetaMender shows nothing at all.
- **After a background scan that changed something**, a small notification
  says how many items were updated: movies, shows, seasons, episodes and
  movie sets, including movies added to or moved between sets and sets
  created. A scan that changed nothing shows nothing.
  Switch these off with **Show notifications**.
- **When something needs you**, a window says so, whatever the notification
  setting.

![The window that appears when something needs you](screenshots/attention/attention-window.png)

The **MetaMender needs you** window appears, when nothing plays, for these
problems:

| The window says | What to do |
|---|---|
| Setup has not been finished yet | Choose **Open setup** and finish it. Nothing is updated before that. |
| There is no TMDB key | Choose **Open setup** and enter your key. |
| Your TMDB key was rejected | Choose **Open setup** and enter the key again; check that it is the API Key, not the Read Access Token. |
| The last background scans did not finish | Background scans failed at least three times over at least a day. The log has the details (see [Troubleshooting](troubleshooting.md)). |
| A folder is unreachable | A library folder has been unreachable for three days or more, for example a network share that is off. Its items are not updated until it is back. |
| MetaMender found a new library | A video source was added to Kodi. It stays out until you include it; choose **Open Libraries**. See [Libraries added to Kodi later](libraries.md#libraries-added-to-kodi-later). |
| Items are in no MetaMender library | Some items are in none of Kodi's video sources, so they are skipped. See [Items in no library](libraries.md#items-in-no-library). |
| MetaMender now has settings per library | Shown once after updating from 1.1. Choose **Open Libraries** to see the result. See [Upgrading from 1.1](libraries.md#upgrading-from-11). |

When there is more than one problem, the most important one is shown and the
others are listed under **Also**. **Later** closes the window; it does not come
back for the same problem, but does for a new one.

If TheTVDB stops accepting MetaMender's own key, a notification says so once
for background scans; Scan now, Update now and a show or season refresh say so
each time they meet it. Titles and plots from TheTVDB are paused until it works again; TMDB carries on.

If fanart.tv rejects its key (MetaMender's or your
[personal key](settings.md#fanarttv)) or cannot be reached, posters whose
choice depends on fanart.tv wait, with nothing changed, and are tried again at
the next scan; everything else carries on. When fanart.tv rejects the key, a
notification says so, and says whether it was your personal key or
MetaMender's own. A background scan shows it once, and again only after
fanart.tv has accepted a key in between; a manual scan, Update now and a show
or season refresh show it each time they meet the rejection. When fanart.tv simply cannot be reached there is no notification;
Kodi's [log](troubleshooting.md#reporting-a-problem) records both.
