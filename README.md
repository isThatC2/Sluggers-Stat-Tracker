# Sluggers Stat Tracker

https://isthatc2.github.io/Sluggers-Stat-Tracker/

An Automatic Stat Tracker MSS on Dolphin

## Disclaimers ⚠️
- Sluggers Stat Tracker supports Windows and Linux. This application utilizes the dolphin-memory-engine Python library, which does not currently support macOS.
An Automatic Stat Tracker for Exhibition Matches in Mario Super Sluggers on Dolphin!

## Disclaimers ⚠️
- Sluggers Stat Tracker supports Windows and Linux. This application utilizes the dolphin-memory-engine python library, which does not currently support macOS.
- Sluggers Stat Tracker is designed for use with Dolphin Emulator only and does not work with other emulators or official hardware.
- Sluggers Stat Tracker does not support modes outside of Exhibition Matches. Playing Challenge Mode, Minigames, etc will cause unintended behavior
- There is a known bug that causes the stat tracker to either fail to detect the start of a match, or false positive detect a match start when it has not yet happened. This appears to be cause by an issue in the dolphin-memory-engine python library, and I am unfortunately unable to fix it at this time. I apologize for the inconvenience. 

## Description
This program is a command line/window application that reads Dolphin memory addresses live during Exhibition Matches in Mario Super Sluggers in order to track game stats live, and later output them into an Excel Workbook. Simply run it in the background while Sluggers is open in Dolphin, enter an Exhiition Match, and the tracker will begin tracking the game's events automatically. 

## Prerequisites
If you are using the portable .exe version found at the link above, you may skip this section. However, if you are using the source code version (.py files), please install the prerequisite python modules specified in requirements.txt

## Installation
Download the latest portable version of the Sluggers Stat Tracker from the link above or download the source code from the latest release.

## Instructions:
- Download the stat tracker & install prerequisite python modules
- Run Dolphin and open Mario Super Sluggers
- Run stat_tracker.py anytime before an exhibition match begins. This can be from the title screen all the way up until to the end of the match start cutscene.
- The stat tracker will wait until it detects a match starting, and will automatically begin tracking stats!
- If the match ends or is quit out early, the stat tracker will output Excel sheet (.xlsx) and a .log file outputting its results
