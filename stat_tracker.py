import time
import sys
import dolphin_memory_engine as dme
from dolphin_mem import Data, read_data, write_data
from MemoryHandling.sluggers_data import Field, Player, Team, Game
from openpyxl import Workbook, load_workbook

team1_score: int = 0
team2_score: int = 0
strikes: int = 0
outs: int = 0
balls: int = 0
batter_index: int = 0
is_replay: bool = False
inning: int = 0
pitches: int = 0
batter: Player
pitcher: Player
last_batter: Player
positions: dict 
inning_changing: bool = False
inning_half = "Top"
#num_baserunners = 0
ball_status: int  = 0
ball_holder: Player | None
ball_is_being_held: bool
runs_this_play = 0
stats_printed = False
ball_hit_flag = False
baserunners: list[Player|None] = [None] * 3
steal_attemptees: list = [None] * 3
at_bat_has_begun = False
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

#----------------------REFRESH/UPDATE GLOBAL VALUES-----------------
def refresh_globals():
    game.current_batter.index.refresh()
    game.current_batter.refresh_all()
    game.current_pitcher.refresh_all()
    team1.pitching_index.refresh()
    team2.pitching_index.refresh()
    team1.meter.refresh()
    team2.meter.refresh()
    game.outs.refresh()
    game.balls.refresh()
    game.strikes.refresh()
    game.current_inning.refresh()
    game.pitches.refresh()
    team1.score.refresh()
    team2.score.refresh()
    game.ball_was_hit.refresh()
    game.batters_this_inning.refresh()
    game.ball_possession.refresh_all()

def update_globals():
    global team1_score, team2_score, outs, balls, strikes, pitches, ball_status, ball_holder, ball_is_being_held
    team1_score = team1.score.value
    team2_score = team2.score.value
    outs = game.outs.value
    balls = game.balls.value
    strikes = game.strikes.value
    ball_status = game.ball_possession.ball_status.value
    ball_holder = game.get_player_having_ball()
    if game.ball_possession.current_ball_holder_index.value >= 0:
        ball_is_being_held = True
    else:
        ball_is_being_held = False

#----------------------HELPER FUNCTIONS-----------------------------
def print_stats(team: Team):
    print(f"{team.branding.display} Stats:")
    for player in team.players:
        s = player.stats
        print("")
        print(f"{player.name}'s Stats:")
        print(f"BATTING STATS:")
        print("---------------------")
        print(f"At Bats: {s.batting.at_bats}")
        print(f"Runs: {s.batting.runs}")
        print(f"RBI: {s.batting.rbi}")
        print(f"Home Runs: {s.batting.home_runs}")
        print(f"Strikeouts: {s.batting.strikeouts}")
        print(f"Walks: {s.batting.walks}")
        print(f"Star Hits: {s.batting.star_hits}")
        print(f"Hit By Pitch: {s.batting.hit_by_pitch}")
        print(f"Singles: {s.batting.singles}")
        print(f"Doubles: {s.batting.doubles}")
        print(f"Triples: {s.batting.triples}")
        print(f"Fly Outs: {s.batting.flyouts}")
        print(f"Ground Outs: {s.batting.ground_outs}")
        print(f"Batting Average: {s.batting.batting_average:.3f}")
        print(f"Slugging Percentage: {s.batting.slugging_percentage:.3f}")
        print(f"Total Bases: {s.batting.total_bases}")
        print(f"On Base Percentage: {s.batting.on_base_percentage:.3f}")
        print(f"On Base Plus Slugging: {s.batting.on_base_slugging:.3f}")
        
        print("FIELDING STATS:")
        print("---------------------")
        print(f"Putouts: {s.fielding.putouts}")
        print(f"Assists: {s.fielding.assists}")
        print(f"Errors: {s.fielding.errors}")
        print(f"Double Plays: {s.fielding.double_plays}")
        print(f"Triple Plays: {s.fielding.triple_plays}")
        print(f"Close Plays Won: {s.fielding.close_plays_won}")
        print(f"Close Plays Lost: {s.fielding.close_plays_lost}")
        print(f"Fielding Chance: {s.fielding.fielding_chances:.3f}")
        
        print("PITCHING STATS:")
        print("---------------------")
        print(f"Innings Pitched: {s.pitching.innings_pitched:.1f}")
        print(f"Earned Runs: {s.pitching.earned_runs}")
        print(f"Strikeouts: {s.pitching.strikeouts}")
        print(f"Star Pitches: {s.pitching.star_pitches}")
        print(f"Batters Faced: {s.pitching.batters_faced}")
        print(f"Home Runs Allowed: {s.pitching.home_runs_allowed}")
        print(f"Hits Allowed: {s.pitching.hits_allowed}")
        print(f"Balls: {s.pitching.balls}")
        print(f"Strikes: {s.pitching.strikes}")
        print(f"Walks: {s.pitching.walks}")
        print(f"Hit By Pitch: {s.pitching.hit_by_pitch}")
        print(f"ERA: {s.pitching.era:.2f}")
        
        print(f"Running Stats:")
        print("---------------------")
        print(f"Steals: {s.running.steals}")
        print(f"Caught Stealing: {s.running.caught_stealing}")
        print(f"Steal Attempts: {s.running.steal_attempts}")
        print(f"Close Plays Won: {s.running.close_plays_won}")
        print(f"Close Plays Lost: {s.running.close_plays_lost}")

def fill_baserunner_list():
    global baserunners
    r = game.baserunners
    runnerlist = [r.first_base, r.second_base, r.third_base]
    
    for i, runner in enumerate(runnerlist):
        if runner.player is not None:
            baserunners[i] = runner.player
        else:
            baserunners[i] = None
            
    
#----------------------REPLAY FUNCTIONS-----------------------------
def check_for_replay():
    """Checks if a replay is currently underway by checking if either a score or out value decreased."""
    global batter_index, outs, team1_score, team2_score
    team1.score.refresh()
    team2.score.refresh()
    game.outs.refresh()
    score_value1: int = team1.score.value
    score_value2: int = team2.score.value
    outs_value: int = game.outs.value
    batter_index_value: int = game.current_batter.index.value or 0

    score_or_out_changed = (
        score_value1 < team1_score
        or score_value2 < team2_score
        or outs_value < outs
    )
    
    print("REPLAY CHECK")
    print(f"score_value1 = {score_value1}, team1.score = {team1.score}")
    print(f"score_value2 = {score_value2}, team1.score = {team2.score}")
    print(f"outs_value = {outs_value}, outs = {outs}")
    
    if batter_index_value == batter_index and score_or_out_changed:
        print("Returning True")
        print("")
        return True
    else:
        print("Returning False")
        print("")
        return False


def replay_state(state):
    """Loop that waits for replays to finish"""
    print("Replay Detected. Pausing Memory Tracking")
    while True:
        state.refresh()
        if (state.display in {
            "LOAD_NEXT_BATTER",
            "END_SCORE_SCREEN",
            "MID_INNING_TRANSITION",
            "RBI_CELEBRATION_CUTSCENE"
        } or state.value not in game.STATE):
            break

    print("Replay Ended")
    isReplay = False
    return 
    
def intro_cutscene_state(state):
    """Stat tracking stat for the intro loading & cutscene states"""
    check_hook_status()
    print(f"{game.team1.branding.display}, VS. {game.team2.branding.display} @ {game.time_of_day.display} {game.map.display}")
    print(game.offense_team.branding.display, "will bat first!")
    
    game.offense_team.meter = Field(0x900d4e24, "u16")
    game.offense_team.score = Field(0x900d5d99, "u8")
    
    game.defense_team.meter = Field(0x900d4e26, "u16")
    game.defense_team.score = Field(0x900d5db3, "u8")
    
    
    while state.display == "INTRO_CUTSCENE":
        state.refresh()
    

def batting_state(state):
    global batter_index, team1_score, team2_score, outs, balls, strikes, pitches, is_replay, batter, pitcher, positions, inning_changing
    global ball_hit_flag, steal_attemptees
    is_replay = check_for_replay()
    if is_replay:
        replay_state(state)
        return
    
    strikeout_recorded = False
    ball_hit_flag = False
    
    offense_meter = game.offense_team.meter.value
    defense_meter = game.defense_team.meter.value
    game.strikes.refresh()
    game.balls.refresh()
    game.outs.refresh()
    game.runs_this_play = 0
    
    game.current_batter.refresh_all()
    game.current_batter.index.refresh()
    batter = game.get_current_batter()
    pitcher = game.get_current_pitcher()
    positions = game.positions.get_all_position_indexes()
    game.positions.refresh_all()
    
    game.set_positions()
    if not game.match_started:
        game.match_started = True
        
    if game.pitches.value != pitches or batter is not game.get_current_batter():
        pitches = game.pitches.value
    
    game.pitches.refresh()
    
    global at_bat_has_begun    
    if game.pitches.value == 0 and not at_bat_has_begun:
        
        print(f"{pitcher.name} Vs. {batter.name}")
        print(game.outs.value, "Outs")
        at_bat_has_begun = True
    
    print(game.balls.value, "-", game.strikes.value)
    
    game.set_positions()
    game.set_baserunners()
    fill_baserunner_list()
    steal_attemptees = [None] * 3
    
    if baserunners[0] is not None:
        print(f"{baserunners[0].name} is on first.")
    
    if baserunners[1] is not None:
        print(f"{baserunners[1].name} is on second.")
    
    if baserunners[2] is not None:
        print(f"{baserunners[2].name} is on third.")
    
        
    while state.display == "BATTING":
        check_hook_status()
        refresh_globals()
        is_replay = check_for_replay()
        if is_replay:
            replay_state(state)
            return
            
        batter = game.get_current_batter()
        pitcher = game.get_current_pitcher()
        
        
        if defense_meter - game.defense_team.meter.value in (50, 100):
            pitcher.stats.pitching.star_pitches += 1
            print(f"{pitcher.name} used a star pitch!")
        
        if game.ball_was_hit.value == 1:
            ball_hit_flag = True
            if offense_meter - game.offense_team.meter.value in (50, 100):
                batter.stats.batting.star_hits += 1
                print(f"{batter.name} used a star hit!")
        
        if game.strikes.value > strikes:
            pitcher.stats.pitching.strikes += 1
            print(f"strike recorded for {pitcher.name}")
        
        if game.balls.value > balls:
            pitcher.stats.pitching.balls += 1
            print(f"ball recorded for {pitcher.name} ")
        
        if game.balls.value == 4 and balls != 4:
            pitcher.stats.pitching.walks += 1
            batter.stats.batting.walks += 1
            print(f"{pitcher.name} walked {batter.name}!")
            
        
        
        if game.outs.value > outs:
            pitcher.stats.pitching.outs_pitched += 1
            if game.strikes.value == 3:
                pitcher.stats.pitching.strikeouts += 1
                batter.stats.batting.strikeouts += 1
                print(f"{pitcher.name} struckout {batter.name}!")
            if game.outs.value == 3:
                inning_changing = True
        
        for runner in baserunners:
            if runner is not None and runner.base is not None:
                runner.base.refresh_all()
                if runner.base.is_stealing.value != 0:
                    if runner not in steal_attemptees:
                        steal_entry = [runner, runner.base.base_num]
                        steal_attemptees.append(steal_entry)
                        runner.stats.running.steal_attempts += 1
                        print(f"{runner.name} is attempting to steal!!")
                        
                        
                    
                
            
        offense_meter = game.offense_team.meter.value
        defense_meter = game.defense_team.meter.value
        update_globals()
        state.refresh()
        
        
        
def fielding_state(state):
    global batter_index, team1_score, team2_score, outs, balls, strikes, pitches, is_replay, batter, pitcher
    global inning_changing, ball_status, ball_is_being_held, ball_holder, runs_this_play
    
    score_change = 0
    last_ball_status = None
    test_ball_held = False
    out_this_tick = False
    caught_out = False
    last_thrower: Player|None = None
    while state.display == "FIELDING":
        check_hook_status()
        refresh_globals()
        holder = game.get_player_having_ball()
        last_holder = game.get_last_player_having_ball()
        ball_status = game.ball_possession.ball_status.value
        isReplay = check_for_replay()
        if isReplay:
                replay_state(state)
                return
        
        
        #Ball Status Shit
        if last_ball_status is not None:
            if  ball_status != last_ball_status:
                time.sleep(0.06)
                last_holder = game.get_last_player_having_ball()
                if ball_status == 1:
                    game.outs.refresh()
                    if game.outs.value > outs:
                        pitcher.stats.pitching.outs_pitched += 1
                        out_this_tick = True
                        
                    if last_holder is not None:
                        #print(f"{last_holder.name} got the ball!")
                        if out_this_tick:
                            print(f"{last_holder.name} recorded a putout!")
                            last_holder.stats.fielding.putouts += 1
                            if last_thrower is not None:
                                last_thrower.stats.fielding.assists += 1
                                print(f"{last_thrower.name} assisted!")
                            #if last_ball_status == 0:
                                #batter.stats.batting.flyouts += 1  
                elif ball_status == 2:
                    if last_holder is not None:
                        print(f"{last_holder.name} threw the ball!")
                        last_thrower = last_holder
                        
                elif ball_status == 3: #Either Error or Attack
                    if last_holder is not None:
                        print(f"{last_holder.name} committed an error (or attacked the ball)!")
                        last_thrower = None
        
        
        if game.offense_team is team1:
            score_change = game.team1.score.value - team1_score
        else:
            score_change = game.team2.score.value - team2_score
        
        if score_change > 0:
            game.runs_this_inning += score_change
            game.runs_this_play += score_change
            batter.stats.batting.rbi += score_change
            pitcher.stats.pitching.earned_runs += score_change
            print(f"score changed by {score_change}! earned runs and rbis recorded")

        out_this_tick = False
        last_ball_status = game.ball_possession.ball_status.value
        update_globals()
        state.refresh()

def mid_inning_transition_state(state):
    global inning_changing, inning_half
    check_hook_status()
    
    print("")
    print(team1.branding.display, "-", team1.score.value)
    print(team2.branding.display, "-", team2.score.value)
    
    if inning_half == "Top":
        inning_half = "Bottom"
    elif inning_half == "Bottom":
        inning_half = "Top"
    print("Changing Sides!")
    
    
    game.runs_this_inning = 0
    #NumBaserunners = 0
    printed = False
    time.sleep(5) #Give time for side change data to settle
    game.set_positions()
    game.current_inning.refresh()
    print(f"Next: {inning_half} of Inning {game.current_inning.value}")
    while state.display == "MID_INNING_TRANSITION":
        refresh_globals()
        batter = game.get_current_batter()
        batter_index = game.get_current_batter_index
        on_deck = game.get_on_deck_batter()
        on_deck_index = game.get_on_deck_batter_index()
        if not printed:
            print("")
            print("Upcoming Batter:", batter.name)
            print("On Deck Batter:", on_deck.name)
            printed = True
        update_globals()        
        state.refresh()
    inning_changing = False



def load_next_batter_state(state):
    global strikes, balls, pitches, inning_changing, batter, pitcher
    global at_bat_has_begun
    check_hook_status()
    at_bat_has_begun = False
    next_batter = game.get_on_deck_batter()
    next_batter_index = game.get_on_deck_batter_index()
    next_on_deck_index = (next_batter_index + 1) % 9
    game.offense_team.batting_index = next_batter_index
    strikes, balls = 0, 0
    game.runs_this_play = 0
    pitcher.stats.pitching.batters_faced += 1
    if not inning_changing:
        next_batter.stats.batting.at_bats += 1
        
        
    while state.display == "LOAD_NEXT_BATTER":
        game.balls.refresh()
        game.strikes.refresh()
        game.pitches.refresh()
        state.refresh()

def end_score_screen_state(state):
    check_hook_status()
    print("Final Score:")
    print(team1.branding.display, "-", team1.score.value)
    print(team2.branding.display, "-", team2.score.value)
    
    if team1.score.value > team2.score.value:
        print(team1.branding.display, "wins!")
    elif team2.score.value > team1.score.value:
        print(team2.branding.display, "wins!")
    else:
        print("The game ended in a tie!")
    
    while state.display == "END_SCORE_SCREEN":
        state.refresh()  

def pause_state(state):
    check_hook_status()
    print("Game Paused...")
    while state.display == "PAUSE":
        refresh_globals()
        update_globals()
        state.refresh()

def end_stat_screen_state(state):
    global stats_printed
    check_hook_status()
    print_stats(team1)
    print_stats(team2)
    stats_printed = True
    while state.display == "END_STAT_SCREEN":
        state.refresh()


def hr_base_celebration_state(state):
    global batter, pitcher
    check_hook_status()
    
    print(f"{batter.name} hits a homer off of {pitcher.name}")
    batter.stats.batting.home_runs += 1
    pitcher.stats.pitching.home_runs_allowed += 1
    
    while state.display ==  "HR_BASE_CELEBRATION" or state.display == "HR_HOMEIN_CELEBRATION":
        state.refresh()

def change_lineup_state(state):
    global pitcher
    check_hook_status()
    print("Defense is Changing the Lineup...")
    
    pre_switch_pitcher = game.get_current_pitcher()
    while state.display == "CHANGE_LINEUP":
        refresh_globals()
        game.set_positions()
        update_globals()
        state.refresh()
    pitcher = pre_switch_pitcher



#------Main Code-------
if __name__ == "__main__":
    team1_addresses = {
        "base_address_list": [0x8131B4B9 + i * 0x8E for i in range(9) ],
        "stamina_address_list": [0x900D61A0 + i * 0x20 for i in range(9)],
        "branding_address": 0x811f76AC,
        "player_type_address": 0x811f76b0,
        "batting_fielding_address": 0x900d5c22,
        "team_number": 1,
        "pitching_index_address": 0x900d5ced #0x900d5ced
    }
    
    team2_addresses = {
        "base_address_list": [0x8131B9B7 + i * 0x8E for i in range(9)],
        "stamina_address_list": [0x900D62C0 + i * 0x20 for i in range(9)],
        "branding_address": 0x811f76AD,
        "player_type_address": 0x811f76b1,
        "batting_fielding_address":0x900d5c23,
        "team_number": 2,
        "pitching_index_address": 0x900d5cc5 #0x900d5cc5
    }
    
    main_loop_on = True
    started_mid_game = False

    while main_loop_on:
        if not dme.is_hooked():
            hook_dolphin()
            
        match_start = detect_match_start()
        
        if match_start is not None:
            if match_start == 2:
                print("It seems a game has already started. Would you like the program to still run?")
                while True:
                    program_input = input("Type 'Y' for Yes or 'N' for No: ").strip().lower()
                    if program_input == 'y':
                        started_mid_game = True
                        break
                    elif program_input == 'n':
                        print("Ending Program.")
                        sys.exit()
                    else:
                        print("Invalid Input. Please type 'Y' or 'N'")
            
        time.sleep(1.5)
        print("Building Team and Game objects...")
        
        team1 = Team(**team1_addresses)
        team2 = Team(**team2_addresses)
        
        
        game = Game(team1, team2)
        
        state = game.game_state
        
        if started_mid_game:
           intro_cutscene_state(state)
        
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
        while game.being_played:
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
                case "HR_BASE_CELEBRATION":
                    hr_base_celebration_state(state)
                case "CHANGE_LINEUP":
                    change_lineup_state(state)
                case "REMATCH":
                    state.refresh()
                case _ if state.value not in game.STATE:
                    print(f"state value is out of range! {state.value}")
                    game.being_played = False
                case _:
                    state.refresh()
                
                    
        print("Game ended. Printing Stats and Deleting Objects:")
        if not stats_printed:
            print_stats(team1)
            print_stats(team2)
        
        del(team1)
        del(team2)
        del(game)
        while True:
            continue_input = input("Continue searching for more games? (Y/N)").strip().lower()
            if continue_input == 'y':
                break
            elif continue_input == 'n':
                print("Ending Program")
                main_loop_on = False
                break
            else:
                print("Invalid response. Please type Y or N")