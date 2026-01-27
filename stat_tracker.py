import time
import sys
import dolphin_memory_engine as dme
from MemoryHandling.dolphin_mem import Data, read_data, write_data
from MemoryHandling.sluggers_data import Player, Team, Game
from openpyxl import Workbook, load_workbook

Team1_Score: int = 0
Team2_Score: int = 0
Strikes: int = 0
Outs: int = 0
Balls: int = 0
Batter_Index: int = 0
isReplay: bool = False
Inning: int = 0
Pitches: int = 0
Batter: Player
Pitcher: Player
On_Deck: Player
Last_To_Hold_Ball: Player

def hook_dolphin():
    while True:
        try:
            if not dme.is_hooked():
                dme.hook()
            if dme.is_hooked():
                print("Dolphin hooked.")
                return True
        except Exception:
            pass
        time.sleep(0.5)
        
def check_hook_status():
    if dme.is_hooked():
        return True
    else:
        print("Program is NOT hooked to Dolphin. closing immediately")
        sys.exit()

def detect_match_start():
    """Keeps checking the game state until a match is starting or already in progress.
     Returns:
        str: "match_starting_now" if a match is starting, "match_already_in_progress" if a match is already in progress.
    """
    print("Waiting for match to begin...")
    state = int.from_bytes(read_data(Data(0x900d5c28, 1)), "big")
    while state not in Game.STATE or state == 0x00:
        state = int.from_bytes(read_data(Data(0x900d5c28, 1)), "big")
    if state == 0x04 or state == 0x05:
        return "match_starting_now"
    else:
        return "match_already_in_progress"
    

def batting_state(state):
    pass

def fielding_state(state):
    pass

def mid_inning_transition_state(state):
    pass

def intro_cutscene_state(state):
    pass

def load_next_batter_state(state):
    pass

def end_score_screen_state(state):
    pass

def pause_state(state):
    pass

def end_stat_screen_state(state):
    pass

def hr_homein_celebration_state(state):
    pass

def change_lineup_state(state):
    pass



#------Main Code-------
if __name__ == "__main__":
    team1_addresses = {
        "base_address_list": [0x8131B4B9 + i * 0x8E for i in range(9) ],
        "stamina_address_list": [0x900D61A0 + i * 0x20 for i in range(9)],
        "branding_address": 0x811f76AC,
        "score_address": 0x900D5D99,
        "meter_address1": 0x9032D93A,
        "meter_address2": 0x900D4E24,
        "player_type_address": 0x811f76b0,
        "batting_fielding_address": 0x900d5c22,
        "team_number": 1
    }
    
    team2_addresses = {
        "base_address_list": [0x8131B9B7 + i * 0x8E for i in range(9)],
        "stamina_address_list": [0x900D62C0 + i * 0x20 for i in range(9)],
        "branding_address": 0x811f76AD,
        "score_address": 0x900D5DB3,
        "meter_address1": 0x9032D93E,
        "meter_address2": 0x900D4E26,
        "player_type_address": 0x811f76b1,
        "batting_fielding_address":0x900d5c23,
        "team_number": 2
    }
   
    while True: 
        if not dme.is_hooked():
            hook_dolphin()
        
        check_hook_status()
        
        match_start = detect_match_start()
        
        if match_start is not None:
            if match_start == "match_already_in_progress":
                print("It seems a game has already started. Would you like the program to still run?")
                while True:
                    program_input = input("Type 'Y' for Yes or 'N' for No: ").strip().lower()
                    if program_input == 'y':
                        break
                    elif program_input == 'n':
                        print("Ending Program.")
                        sys.exit()
                    else:
                        print("Invalid Input. Please type 'Y' or 'N'")
            else:
                print("Match is starting now!")
                time.sleep(1) # Give time for the game to fully load

        match_number = 1
        print("Initializing Game, Team, and Player Data...")
        team1 = Team(**team1_addresses)
        team2 = Team(**team2_addresses)
        game = Game(team1, team2)
        
        state = game.game_state
        
        stat_sheet = Workbook()
        while game.being_played and state in Game.STATE:
            match state.display:
                case "BATTING":
                    batting_state(state)
                case "FIELDING":
                    fielding_state(state)
                case "MID_INNING_TRANSITION":
                    mid_inning_transition_state(state)
                case "INTRO_CUTSCENE":
                    intro_cutscene_state(state)
                case "LOAD_NEXT_BATTER":
                    load_next_batter_state(state)
                case "END_SCORE_SCREEN":
                    end_score_screen_state(state)
                case "PAUSE":
                    pause_state(state)
                case "END_STAT_SCREEN":
                    end_stat_screen_state(state)
                case "HR_HOMEIN_CELEBRATION":
                    hr_homein_celebration_state(state)
                case "CHANGE_LINEUP":
                    change_lineup_state(state)
                case "REMATCH":
                    state.refresh()
                    pass
                case _:
                    state.refresh()