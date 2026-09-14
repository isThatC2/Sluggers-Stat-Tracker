# Sluggers Stat Tracker

https://isthatc2.github.io/Sluggers-Stat-Tracker/

An Automatic Stat Tracker for MSS on Dolphin

Special thanks to jackharrhy for setting up the download site!

## Disclaimers ⚠️
- Sluggers Stat Tracker supports Windows and Linux. This application utilizes the dolphin-memory-engine Python library, which does not currently support macOS.
- Sluggers Stat Tracker is designed for use with Dolphin Emulator only and does not work with other emulators or official hardware.
- Sluggers Stat Tracker does not support modes outside of Exhibition Matches. Playing Challenge Mode, Minigames, etc. will cause unintended behavior.
- There is a known bug that causes the stat tracker to either fail to detect the start of a match or falsely detect a match start before it happens. This appears to be caused by an issue in the dolphin-memory-engine Python library, and I am unfortunately unable to fix it at this time. More info is at the end of the README.

## Description
This program is a command-line/windowed application that reads Dolphin memory addresses live during Exhibition Matches in Mario Super Sluggers in order to track game stats live and later output them into an Excel Workbook. Simply run it in the background while Sluggers is open in Dolphin, enter an Exhibition Match, and the tracker will begin tracking the game's events automatically. 

## Prerequisites
If you are using the portable .exe version found at the link above, no prerequisites are required other than Dolphin Emulator and a copy of Mario Super Sluggers (USA version). However, if you are using the source code version (.py files), please install the prerequisite Python modules specified in requirements.txt.

## Installation
Download the [latest portable version](https://isthatc2.github.io/Sluggers-Stat-Tracker/) of the Sluggers Stat Tracker, or download the source code from the repo (not recommended).

## Instructions
- Download the stat tracker.
- Run Dolphin and open Mario Super Sluggers.
- Run stat_tracker.py anytime before an exhibition match begins. This can be done anywhere from the title screen up until the end of the match start cutscene.
- The stat tracker will wait until it detects a match starting and will automatically begin tracking stats!
- That's it! You can view the play-by-play logs in the terminal or you can minimize it until the game is over.
- Once the match ends and the game reaches the MVP screen (or if the match is ended early), the stat tracker will output its original Excel sheet (.xlsx), an additive `- Advanced.xlsx` workbook, and its original .log file.
- The tracker will automatically start tracking a new game if you click the rematch button, otherwise if you exit to the main menu, the tracker will ask you if you want to track another game.

## Advanced Excel Workbook

Advanced tracking starts automatically with each match. It runs in an isolated background process, and it does not require a server, account, API key, internet connection, or separate command. The original workbook remains unchanged. The second workbook contains these sheets:

- **Game Summary** - match identity and tracker schema version.
- **Batted Balls** - exit velocity, launch angle, spray angle, distance, hang time, and landing/catch point.
- **Fielding** - first touch, route efficiency, reaction, sprint speed, and measured catch approach.
- **Baserunning** - speed, path, lead, home-to-first time, and normalized 90-foot split.
- **Throws** - throw chain, velocity, release time, flight time, and endpoints.
- **Pitches** - pitcher, batter, offer, outcome, and any validated pitch measurements available for that pitch.
- **Capture Health** - frame loss and calibration status so unavailable measurements are visible rather than guessed.

This build contains no stadium-specific measurement at all: no park geometry, no gimmick or hazard detection, and no attribution of a play to a stadium object. An affected metric is left blank when the capture missed required frames, player-position calibration did not confirm, or forced/immobilized movement would make that number misleading. If advanced tracking cannot start or export, the original tracker and original workbook continue normally.

The advanced workbook can report a slightly higher pitch count than the original one, and the original is the one that is low. The original tracker increments its pitch counter inside the `BATTING` state loop, so a pitch thrown while the game has briefly left that state is not counted -- in a measured three-inning test the two disagreed by exactly three, all of them two-strike foul balls. The advanced worker reads every frame regardless of game state, so it sees them. Nothing else in the original workbook is affected.

Advanced memory addresses currently support the USA game revision (`R4QE01`) only. A real Dolphin match is still the required final compatibility check for each release build.

## Custom Team Names
Want to change the team names in the stat tracker for your league? Sluggers Stat Tracker supports custom team names!
- Go to your extracted folder
- Open the MemoryHandling folder. On a new install, rename `team_branding.example.json` to `team_branding.json`, then open it in a text editor. Update ZIPs deliberately do not contain `team_branding.json`, so an existing customization is not overwritten.
- Change the full and short team name to your name of choice. For example, to change the Mario Fireballs to the Delfino Sunburns, you would replace "Mario Fireballs" with Delfino Sunburns and "Fireballs" with Sunburns

## My Stat Tracker Isn't Detecting Match Start / Is Detecting a Match Start That Didn't Happen
If your stat tracker seems to be unresponsive or misbehaving, it is likely due to an issue with the dolphin-memory-engine Python module. Since it is not an issue caused by the stat tracker itself, I am unable to fix it directly. I have, however, found a workaround for this issue.

If at any point your stat tracker fails to detect a match properly:
1. **MAKE A SAVE STATE!** — Especially if a match has already been initiated. The stat tracker should instantly recognize a match has begun once you press the Start Button on the Match Rules Select Screen. If the intro cutscene has started playing and the tracker log has not printed "match is starting now!", it is reading incorrect data and will not function correctly. Making a save state will save time setting up teams again!
2. Close Mario Super Sluggers, but **DO NOT** close Dolphin.
3. Go to Config -> Advanced -> Enable MMU, then turn it on and back off a few times, leaving it off in the end.
4. Close Settings and reopen Mario Super Sluggers.
5. Load the save state you made previously.

In my experience developing the stat tracker, this has worked without fail. You can also do this process before attempting to use the stat tracker (opening and then immediately closing Sluggers, then following steps 2–4).

Enjoy!
