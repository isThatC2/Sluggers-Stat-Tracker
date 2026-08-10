# Sluggers Stat Tracker

https://isthatc2.github.io/Sluggers-Stat-Tracker/

An Automatic Stat Tracker MSS on Dolphin

Special thanks to jackharrhy for setting up the download site!!

## Disclaimers ⚠️
- Sluggers Stat Tracker supports Windows and Linux. This application utilizes the dolphin-memory-engine python library, which does not currently support macOS.
- Sluggers Stat Tracker is designed for use with Dolphin Emulator only and does not work with other emulators or official hardware.
- Sluggers Stat Tracker does not support modes outside of Exhibition Matches. Playing Challenge Mode, Minigames, etc will cause unintended behavior
- There is a known bug that causes the stat tracker to either fail to detect the start of a match, or false positive detect a match start when it has not yet happened. This appears to be cause by an issue in the dolphin-memory-engine python library, and I am unfortunately unable to fix it at this time. More info is at the end of the README.

## Description
This program is a command line/window application that reads Dolphin memory addresses live during Exhibition Matches in Mario Super Sluggers in order to track game stats live, and later output them into an Excel Workbook. Simply run it in the background while Sluggers is open in Dolphin, enter an Exhiition Match, and the tracker will begin tracking the game's events automatically. 

## Prerequisites
If you are using the portable .exe version found at the link above, no prerequisites are required other than Dolplin Emulator and a copy of Mario Super Sluggers (USA version). However, if you are using the source code version (.py files), please install the prerequisite python modules specified in requirements.txt

## Installation
Download the [latest portable version](https://isthatc2.github.io/Sluggers-Stat-Tracker/) of the Sluggers Stat Tracker or download source code from the repo (not recommended).

## Instructions:
- Download the stat tracker
- Run Dolphin and open Mario Super Sluggers
- Run stat_tracker.py anytime before an exhibition match begins. This can be from the title screen all the way up until to the end of the match start cutscene.
- The stat tracker will wait until it detects a match starting, and will automatically begin tracking stats!
- If the match ends or is quit out early, the stat tracker will output Excel sheet (.xlsx) and a .log file outputting its results

## My Stat Tracker Isn't Detecting Match Start/Is Detecting a Match Start That Didn't Happen
If your stat tracker seems to be either unresponsive, it is likely due to an issue with the dolphin-memory-engine Python module. Unfortunately, since it is not an issue caused by the stat tracker itself, I am unable to fix it. I have however, found a way to seemingly band-aid this issue

If at any point, your stat tracker seems to be failing to detect a match properly,
1. **MAKE A SAVE STATE!** - Especially if a match has already been initiated, the stat tracker should instantly recognize a match has begun once you press the Start Button on the Match Rules Select Screen, If the intro cutscene has started playing and the tracker log has not printed "match is starting now!", it is reading incorrect data and will not function correctly. Making a save state will save time setting up teams again!
2. Close the Mario Super Sluggers game but **DO NOT** close Dolphin.
3. Go to Config -> Advanced -> Enable MMU and turn it on and back off again a few times, leaving it off in the end.
4. Close Settings and Reopen Mario Super Sluggers.
5. Load the save state you made previously.

In my experience developing the stat tracker, this has worked without fail. You can also do this process before attempting to use the stat tracker (opening and then immediately closing Sluggers, then following steps 2-4). I found that to work as well.

Enjoy!
