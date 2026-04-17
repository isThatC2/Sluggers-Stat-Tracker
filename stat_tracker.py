import time
import sys
import logging
import dolphin_memory_engine as dme
import outputter as out
from dolphin_mem import Data, read_data
from MemoryHandling.sluggers_data import Field, Player, Team, Game, NO_PLAYER, NO_TEAM
from enum import Enum

# Used to track the last pitch's ID that was processed to avoid processing the same pitch multiple times during replays.
last_pitch_id_processed = -1


logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)


# Enums and Classes to hold information about the current status of the ball that can be easily passed around and updated.
class BallPossessionStatus(Enum):
    UNFIELDED = 0
    HELD = 1
    THROWN = 2
    LOOSE = 3


class BallLandingStatus(Enum):
    FOUL = -1
    AIRBORNE = 0
    FAIR = 1
    FAIR_FIELDED = 2
    CAUGHT_OUT = 3



# Holds relavent live match information that can be easily passed around and updated.
class MatchContext:
    def __init__(self):
        self.team1_score: int = 0
        self.team2_score: int = 0
        self.team1_hits: int = 0
        self.team2_hits: int = 0
        self.offense_team: Team = NO_TEAM
        self.defense_team: Team = NO_TEAM
        self.strikes: int = 0
        self.outs: int = 0
        self.balls: int = 0
        self.batter_index: int = 0
        self.inning: int = 0
        self.pitches: int = 0
        self.pitch_id: int = 0
        self.batter: Player = NO_PLAYER
        self.pitcher: Player = NO_PLAYER
        self.positions: dict = {}
        self.inning_half: str = "Top"
        self.num_baserunners: int = 0
        self.ball_status: BallPossessionStatus = BallPossessionStatus.UNFIELDED
        self.ball_holder: Player = NO_PLAYER
        self.buddy_jumper: Player = NO_PLAYER
        self.runs_this_pitch: int = 0
        self.outs_this_pitch: int = 0
        self.baserunners: list[Player] = [NO_PLAYER] * 4
        self.steal_attempters: list[tuple[Player, int]] = []
        self.runners_scored_this_pitch: list[Player] = []
        self.pitchers_thrown_this_at_bat: list[Player] = []
        self.fielders_touched_ball_this_pitch: list[Player] = []
        self.inherited_baserunners: list[tuple[Player, Player]] = []
        self.defense_lineup: dict[Player, str] = {}
        self.offense_team_score: int = 0
        self.num_bases_ran: int = 0
        self.out_contributors: list[Player] = []
        self.ball_is_being_held: bool = False
        self.is_replay: bool = False
        self.inning_changing: bool = False
        self.stats_outputted: bool = False
        self.ball_hit_flag: bool = False
        self.plate_appearance_begun: bool = False
        self.plate_appearance_completed: bool = False
        self.runs_earned_this_pitch: bool = True
        self.runs_earned_this_inning: bool = True
        self.at_bat_eligible: bool = True
        self.home_run_flag: bool = False
        self.sacrifice_flag: bool = False
        self.bean_ball_flag: bool = False
        self.pickoff_attempt_flag: bool = False
        self.pitch_thrown_flag: bool = False


# Holds a snapshot of the game state at the time of each pitch. Used to check for changes that can't be tracked live during the pitch and update stats accordingly at the start of the next pitch.
class PitchSnapshot:
    def __init__(self, balls: int, strikes: int, outs: int = 0, pitches: int = 0, batter: Player = NO_PLAYER, pitcher: Player = NO_PLAYER,
                 baserunners: list[Player] = [], steal_attempters: list[tuple[Player, int]] = [],
                 runners_score_this_pitch: list[Player] = [], out_contributors: list[Player] = [],
                 offense_team: Team = NO_TEAM, defense_team: Team = NO_TEAM,
                 num_bases_ran: int = 0, offense_hits: int = 0, offense_score: int = 0,
                 runs_this_pitch: int = 0, outs_this_pitch: int = 0, ball_hit_flag: bool = False,
                 home_run_flag: bool = False, sacrifice_flag: bool = False, bean_ball_flag: bool = False, pitch_id: int = 0):
        self.balls = balls
        self.strikes = strikes
        self.outs = outs
        self.pitches = pitches
        self.batter = batter
        self.pitcher = pitcher
        self.pitches = pitches
        self.pitch_id = pitch_id
        self.baserunners = baserunners if baserunners is not None else []
        self.steal_attempters = steal_attempters if steal_attempters is not None else []
        self.runners_score_this_pitch = runners_score_this_pitch if runners_score_this_pitch is not None else []
        self.out_contributors = out_contributors if out_contributors is not None else []
        self.offense_team = offense_team
        self.defense_team = defense_team
        self.num_bases_ran = num_bases_ran
        self.offense_hits = offense_hits
        self.offense_score = offense_score
        self.runs_this_pitch = runs_this_pitch
        self.outs_this_pitch = outs_this_pitch
        self.ball_hit_flag = ball_hit_flag
        self.home_run_flag = home_run_flag
        self.sacrifice_flag = sacrifice_flag
        self.bean_ball_flag = bean_ball_flag
    
    

# Unused, but  may be useful in the future for performance issues for some people?
# Program Setting Variables
tick_wait_time = 0  # Time the program waits between each tick of a loop. Try not to go above 0.3 seconds-ish
max_wait_for_hook_mins = 20  # Maximum amount in minutes of time the program will attempt to hook to dolphin before closing itself


def hook_dolphin():
    """Initial hook to Dolphin process"""
    while True:
        try:
            if not dme.is_hooked():
                dme.hook()
            if dme.is_hooked():
                log.info("Dolphin hooked.")
                return True
        except Exception:
            pass
        time.sleep(0.5)

def check_hook_status():
    if dme.is_hooked():
        return True
    else:
        log.critical("Program is NOT hooked to Dolphin. Closing immediately.")
        sys.exit()

def detect_match_start():
    """Keeps checking the game state until a match is starting or already in progress.
     Returns:
        1 if a match is starting now, 2 if a match is already in progress or misread.
    """
    log.info("Waiting for match to begin...")
    state = int.from_bytes(read_data(Data(0x900d5c28, 1)), "big")
    while state not in Game.STATE or state == 0x00:
        state = int.from_bytes(read_data(Data(0x900d5c28, 1)), "big")
    if state == 0x04 or state == 0x05:
        return 1  # Match Starting Now!
    else:
        return 2  # Match Already in Progress or Misread

# ----------------------REFRESH/UPDATE VALUES-----------------
def _refresh_game_values(game: Game, team1: Team, team2: Team):
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
    game.this_pitch.refresh_all()
    game.star_costs.refresh_all()
    game.left_buddy_jump_flag.refresh()
    game.center_buddy_jump_flag.refresh()
    game.right_buddy_jump_flag.refresh()

def _update_match_context_values(game: Game, team1: Team, team2: Team, mc: MatchContext):
    mc.team1_score = team1.score.value
    mc.team2_score = team2.score.value
    mc.outs = game.outs.value
    mc.balls = game.balls.value
    mc.strikes = game.strikes.value
    mc.ball_status = game.ball_possession.ball_status.value
    mc.outs_this_pitch = game.this_pitch.outs.value
    mc.runs_this_pitch = game.this_pitch.runs.value
    mc.ball_holder = game.get_current_ball_holder()
    mc.offense_team_score = game.offense_team.score.value
    mc.ball_is_being_held = game.ball_possession.current_ball_holder_index.value >= 0
    mc.num_bases_ran = game.this_pitch.num_bases_ran.value

def _make_pitch_snapshot(mc: MatchContext) -> PitchSnapshot:
    return PitchSnapshot(
        balls=mc.balls,
        strikes=mc.strikes,
        outs=mc.outs,
        batter=mc.batter,
        pitcher=mc.pitcher,
        pitches=mc.pitches,
        pitch_id =mc.pitch_id,
        baserunners=mc.baserunners.copy(),
        steal_attempters=mc.steal_attempters.copy(),
        runners_score_this_pitch=mc.runners_scored_this_pitch.copy(),
        out_contributors=mc.out_contributors.copy(),
        num_bases_ran=mc.num_bases_ran,
        offense_hits=mc.team1_hits if mc.offense_team is game.team1 else mc.team2_hits,
        offense_score=mc.team1_score if mc.offense_team is game.team1 else mc.team2_score,
        offense_team=mc.offense_team,
        defense_team=mc.defense_team,
        runs_this_pitch=mc.runs_this_pitch,
        outs_this_pitch=mc.outs_this_pitch,
        ball_hit_flag=mc.ball_hit_flag,
        home_run_flag=mc.home_run_flag,
        sacrifice_flag=mc.sacrifice_flag,
        bean_ball_flag=mc.bean_ball_flag
    )
    

# ---------------------- GENERAL HELPER FUNCTIONS-----------------------------
def wait_for_tick():
    if tick_wait_time <= 0:
        return
    time.sleep(tick_wait_time)


def record_if_increased(new_val, old_val, *stat_fields: tuple, label=None) -> bool:
    if new_val > old_val:
        for obj, attr in stat_fields:
            setattr(obj, attr, getattr(obj, attr) + 1)
        if label:
            log.info(label)
        return True
    return False


def output_stats_to_excel(game: Game):
    outputter = out.Outputter("Stat_Template_DO_NOT_REMOVE.xlsx", game)
    outputter.output_game(game)
    

def _assign_score_and_meter_fields(game):
    """Correctly assigns score and meter fields to home and away teams at match-start"""
    game.inning_half.refresh()

    if game.inning_half.value == 0:
        a = game.offense_team
        b = game.defense_team
    else:
        a = game.defense_team
        b = game.offense_team

    a.meter = Field(0x900d4e24, "u16")
    a.score = Field(0x900d5d98, "u16")

    b.meter = Field(0x900d4e26, "u16")
    b.score = Field(0x900d5db2, "u16")
    
    a.hits= Field(0x900d5dcd, "u8")
    b.hits = Field(0x900d5de7, "u8")
    
    

def _fill_baserunner_list(game: Game, mc: MatchContext):
    r = game.baserunners
    r.refresh_all()
    runnerlist = [r.first_base, r.second_base, r.third_base]

    for runner in runnerlist:
        if runner.player is not NO_PLAYER and runner.base_num is not None:
            mc.baserunners[runner.base_num] = runner.player
        else:
            mc.baserunners[runner.base_num] = NO_PLAYER

# ---------------------- BATTING STATE HELPER FUNCTIONS-----------------------------


def __set_starting_lineup(game: Game, team: Team):
    """Creates a dict of the players at each defensive position for a given team at the start of the match"""
    
    lineup = game.def_positions.get_all_players_at_positions()
    team.starting_lineup = lineup
    log.debug(f"{team.short_name} Starting Lineup Set!")
    team.starting_lineup_set = True


def __update_positions_played(game: Game, team: Team, mc: MatchContext):
    lineup = mc.defense_lineup
    for player in team.players:
        position = lineup.get(player)
        if position is not None and position not in player.stats.positions_played:
            player.stats.positions_played.append(position)
        
    
    

def _check_count_changes(game: Game, pitcher: Player, batter: Player, mc: MatchContext):
    """Updates stats if balls. strikes, or out values increase"""
    ball_happened = record_if_increased(game.balls.value, mc.balls, 
                        (mc.pitcher.stats.pitching, "balls"),
                        label = f"Ball {game.balls.value}.")
    
    strike_happened = record_if_increased(game.strikes.value, mc.strikes,
                        (mc.pitcher.stats.pitching, "strikes"),
                        label = f"Strike {game.strikes.value}.")
    
    out_happened = record_if_increased(game.outs.value, mc.outs,
                        (mc.pitcher.stats.pitching, "outs_pitched"),
                        label = f"{mc.pitcher.name} got an out! That's {game.outs.value}!")
    
    if ball_happened and game.balls.value == 4:
        mc.at_bat_eligible = False
        mc.pitcher.stats.pitching.walks += 1
        mc.batter.stats.batting.walks += 1
        log.info(f"{mc.pitcher.name} walked {mc.batter.name}!")
        mc.plate_appearance_completed = True
        mc.at_bat_eligible = False
        log.info(f"{mc.batter.name}'s plate appearance completed with a walk.")
        
        if mc.num_baserunners == 3:
            log.info("Bases loaded walk!")
            mc.batter.stats.batting.rbi += 1
            mc.baserunners[3].stats.running.runs += 1
            mc.pitcher.stats.pitching.runs_allowed += 1
            mc.pitcher.stats.pitching.earned_runs += 1
    
    if strike_happened and game.strikes.value == 3:
        mc.pitcher.stats.pitching.strikeouts += 1
        mc.batter.stats.batting.strikeouts += 1
        catcher = game.def_positions.catcher.player
        if catcher is not NO_PLAYER:
            catcher.stats.fielding.putouts += 1
        log.info(f"{mc.pitcher.name} struck out {mc.batter.name}!")
        mc.at_bat_eligible = True
        mc.plate_appearance_completed = True
        log.info(f"{mc.batter.name}'s plate appearance completed with a strikeout.")


def _check_if_steal_attempt(game: Game, runner: Player, base_num: int, mc: MatchContext):
    """Checks if a runner is attempting to steal a base"""
    if runner.base is None:
        return
    
    runner.base.refresh_all()
    if runner.base.is_stealing.value != 0:
        steal_entry = (runner, runner.base.base_num)
        if steal_entry not in mc.steal_attempters:
            mc.steal_attempters.append(steal_entry)
            runner.stats.running.steal_attempts += 1
            log.info(f"{runner.name} is attempting to steal!")

def _check_if_steal_success(game: Game, runner: Player, base_num: int, mc: MatchContext):
    """Checks at the start of a new pitch if players in steal attempters list are at a further base than last pitch"""
    game.set_baserunners()
    if runner is NO_PLAYER:
        return
    
    steal_entry = (runner, base_num)
    if steal_entry in mc.steal_attempters:
        index = mc.steal_attempters.index(steal_entry)
        player, steal_base_num = mc.steal_attempters[index]
        
        if runner.base is None:
            log.warning(f"{runner.name} may have scored or gotten out. functionality not there yet")
            return
        
        if player is runner and (runner.base.base_num > steal_base_num) or player in mc.runners_scored_this_pitch:
            log.info(f"{runner.name} stole a base!")
            runner.stats.running.stolen_bases += 1


def _check_for_bean_ball(game: Game, mc: MatchContext):
    """Checks if the batter was hit by a pitch and awards the stat"""
    if game.this_pitch.bean_ball_flag.value == 1 and not mc.bean_ball_flag:
        mc.batter.stats.batting.hit_by_pitch += 1
        mc.pitcher.stats.pitching.bean_balls += 1
        log.info(f"{mc.batter.name} was hit by a pitch!")
        mc.bean_ball_flag = True
        mc.at_bat_eligible = False
        mc.plate_appearance_completed = True
        #log.info(f"{mc.batter.name}'s plate appearance completed with a hit by pitch.")
        
        if mc.num_baserunners == 3:
            log.info("Bases loaded hit by pitch!")
            _record_score_change(game, mc, sacrifice_possible=False)
            mc.batter.stats.batting.rbi += 1
            mc.baserunners[3].stats.running.runs += 1
            mc.pitcher.stats.pitching.runs_allowed += 1
            mc.pitcher.stats.pitching.earned_runs += 1
    
def _check_if_ball_was_hit(game: Game, offense_meter: int, mc: MatchContext):
    """Checks if the batter hit the ball, adds the batters player objects to baserunners list
        and clears steal attempters list"""
    
    game.ball_was_hit.refresh()
    if game.ball_was_hit.value == 1 and not mc.ball_hit_flag:
        mc.baserunners[0] = mc.batter
        mc.num_baserunners = sum(x is not NO_PLAYER for x in mc.baserunners)
        #log.debug(f"{mc.baserunners[0].name} - {mc.num_baserunners}")
        mc.steal_attempters = []
        mc.ball_hit_flag = True

        
            

def _check_for_star_hit_usage(game: Game, offense_meter: int, mc: MatchContext):
    """Checks if the batter hit the ball with a star hit and awards the stat"""
    sc = game.star_costs
    game.offense_team.meter.refresh()
    if offense_meter - game.offense_team.meter.value in (sc.captain_star_cost.value, sc.non_main_captain_star_cost.value, sc.regular_star_cost.value):
        mc.batter.stats.batting.star_hits += 1
        log.info(f"{mc.batter.name} used a star hit!")

def _check_for_star_pitch_usage(game: Game, defense_meter: int, mc: MatchContext):
    """Checks if the pitcher threw a star hit and awards the stat"""
    sc = game.star_costs
    game.defense_team.meter.refresh()
    if defense_meter - game.defense_team.meter.value in (sc.captain_star_cost.value, sc.non_main_captain_star_cost.value, sc.regular_star_cost.value):
        mc.pitcher.stats.pitching.star_pitches += 1
        log.info(f"{mc.pitcher.name} used a star pitch!")

def _process_last_pitch_snapshot(game: Game, mc: MatchContext, last_pitch: PitchSnapshot):
    """Checks for changes since last pitch that currently can't be tracked live and updates stats accordingly."""
    if last_pitch is None:
        print(f"Last Pitch doesn't exist")
        return
    
    if last_pitch.pitch_id == mc.pitch_id:
        print(f"Last Pitch already processed")
        return
    
    #hit_recorded = _check_if_hit_was_recorded(game, game.team1, game.team2, mc)
    
    if last_pitch.offense_hits < (mc.team1_hits if last_pitch.offense_team is game.team1 else mc.team2_hits):
        log.debug(f"{last_pitch.batter.name} recorded a hit!")
        last_pitch.batter.stats.batting.hits += 1
        last_pitch.pitcher.stats.pitching.hits_allowed += 1
        log.debug(f"{last_pitch.batter.name} has {last_pitch.batter.stats.batting.hits} hits!")
        log.debug(f"{last_pitch.pitcher.name} has allowed {last_pitch.pitcher.stats.pitching.hits_allowed} hits!")
        
        if last_pitch.num_bases_ran == 3:
            last_pitch.batter.stats.batting.triples += 1
            log.debug(f"{last_pitch.batter.name} recorded their {last_pitch.batter.stats.batting.triples} triples!")
            
        elif last_pitch.num_bases_ran == 2:
            last_pitch.batter.stats.batting.doubles += 1
            log.debug(f"{last_pitch.batter.name} has {last_pitch.batter.stats.batting.doubles} doubles!")
        elif last_pitch.num_bases_ran == 1:
            last_pitch.batter.stats.batting.singles += 1
            log.debug(f"{last_pitch.batter.name} has {last_pitch.batter.stats.batting.singles} singles!")
            
        elif last_pitch.num_bases_ran == 4 and not last_pitch.home_run_flag:
            
            last_pitch.batter.stats.batting.home_runs += 1
            last_pitch.batter.stats.batting.inside_the_park_home_runs += 1
            last_pitch.pitcher.stats.pitching.home_runs_allowed += 1
            
            log.debug(f"{last_pitch.batter.name} recorded an inside the park home run!")
    





    
# ---------------------- FIELDING STATE HELPER FUNCTIONS----------------------------- 

def __check_for_buddy_jump(game: Game) -> Player:
    """Checks if a buddy jump occurred and updates mc.buddy_jumper accordingly"""
    if game.left_buddy_jump_flag.value == 1:
        return game.def_positions.left_field.player
    elif game.center_buddy_jump_flag.value == 1:
        return game.def_positions.center_field.player
    elif game.right_buddy_jump_flag.value == 1:
        return game.def_positions.right_field.player
    else:
        return NO_PLAYER


def _check_ball_status_change(ball_status: BallPossessionStatus, last_ball_status: BallPossessionStatus, game: Game) -> tuple[Player, Player, Player]:
    """Checks if the ball status changed and logs accordingly.
    Returns (last_holder, last_thrower)"""
    new_thrower = NO_PLAYER
    current_ball_holder = game.get_current_ball_holder()
    last_ball_holder = game.get_last_player_to_touch_ball()
    get_current_ball_holder_refreshes = 0
    if last_ball_status != ball_status:
        last_ball_holder = game.get_last_player_to_touch_ball()
        if ball_status == BallPossessionStatus.HELD:
            while current_ball_holder == NO_PLAYER and get_current_ball_holder_refreshes < 100:
                game.ball_possession.refresh_all()
                current_ball_holder = game.get_current_ball_holder()
                get_current_ball_holder_refreshes += 1
            
            if current_ball_holder is NO_PLAYER:
                log.warning(f"Ball possession status changed to HELD but no current ball holder found after refreshing. This may be a misread or a very fast change. Last ball holder: {last_ball_holder.name if last_ball_holder is not NO_PLAYER else 'None'}")
            else:
                pass
                #log.debug(f"{current_ball_holder.name} got the ball.")
        elif ball_status == BallPossessionStatus.THROWN:
            #log.debug(f"LAST BALL HOLDER = {last_ball_holder.name}")
            new_thrower = last_ball_holder
            if new_thrower is not NO_PLAYER:
                pass
                #log.debug(f"{new_thrower.name} threw the ball.")
        elif ball_status == BallPossessionStatus.LOOSE:
            #log.debug(f"BLUNDER OCCURRED!!!")
            new_thrower = NO_PLAYER
                
    return current_ball_holder, last_ball_holder, new_thrower


def _check_landing_status(landing_status: BallLandingStatus, last_landing_status: BallLandingStatus, mc: MatchContext) -> bool:
    """Logs the landing status of the ball and records foul balls.
    Returns True if landing status was newly recorded, False if already recorded or still airborne."""
    if landing_status == BallLandingStatus.AIRBORNE:
        return False
    
    if landing_status == last_landing_status:
        return False
    
    match landing_status:
        case BallLandingStatus.FOUL:
            log.info("Foul ball!")
        case BallLandingStatus.FAIR:
            log.info("Fair ball!")
        case BallLandingStatus.FAIR_FIELDED:
            log.info("Fair ball fielded!")
        case BallLandingStatus.CAUGHT_OUT:
            log.info(f"{mc.batter.name}'s hit was caught!")
    
    return True

def _record_out(game: Game, mc: MatchContext, last_holder: Player, last_thrower: Player, landing_status: BallLandingStatus):
    """Records putouts, assists, and batter out type for a newly recorded out."""
    match game.this_pitch.outs.value:
        case 2:
            log.info("Double play!")
        case 3:
            log.info("Triple play!")
    
    
    #log.debug(f"There are currently {game.this_pitch.outs.value} outs recorded for this pitch.")
    out_runner_id = game.this_pitch.get_out_runner_by_num(game.this_pitch.outs.value)
    out_runner = mc.baserunners[out_runner_id.value]
    
    
    #log.debug(f"There are currently {game.this_pitch.outs.value} outs recorded for this pitch.")
    #log.debug(f"Out runner ID: {out_runner_id.value}, Out runner: {out_runner.name if out_runner is not NO_PLAYER else 'None'}")

    # Person holding ball occasionally updates after the out is recorded.
    # Keep refreshing until it does update.
    
    while last_holder is NO_PLAYER or out_runner is NO_PLAYER:
        last_holder = game.get_current_ball_holder()
        out_runner_id.refresh()
        out_runner = mc.baserunners[out_runner_id.value]
        if last_holder is NO_PLAYER:
            log.debug(f"Waiting for LAST HOLDER to update for out... last holder: {last_holder.name if last_holder is not NO_PLAYER else 'None'}, out runner ID: {out_runner_id.value}, out runner: {out_runner.name if out_runner is not NO_PLAYER else 'None'}")
        if out_runner is NO_PLAYER:
            log.debug(f"Waiting for OUT RUNNER to update for out.. last holder: {last_holder.name if last_holder is not NO_PLAYER else 'None'}, out runner ID: {out_runner_id.value}, out runner: {out_runner.name if out_runner is not NO_PLAYER else 'None'}")

    log.info(f"{last_holder.name} got {out_runner.name} out!")
    
    # Checks for if out can be classified as a specific type of out and awards stats accordingly.
    if last_holder is mc.buddy_jumper:
        log.info(f"{last_holder.name} went high up with the buddy jump to get the out!")
        last_holder.stats.fielding.buddy_jump_outs += 1
    
    if mc.pickoff_attempt_flag:
        log.info(f"{mc.pitcher.name} picked off {out_runner.name}!")
        mc.pitcher.stats.pitching.pickoffs += 1
        mc.pickoff_attempt_flag = False
    
    if out_runner in mc.steal_attempters:
        log.info(f"{out_runner.name} was caught stealing!")
        out_runner.stats.running.caught_stealing += 1
    
    
    
    last_holder.stats.fielding.putouts += 1
    if last_holder not in mc.out_contributors:
        mc.out_contributors.append(last_holder)
        #log.info(f"{last_holder.name} contributed to the out!")
    
    for player in mc.fielders_touched_ball_this_pitch:
        if player is not last_holder and player is not NO_PLAYER:
            print(f"{player.name} recorded an assist!")
            player.stats.fielding.assists += 1
            if player not in mc.out_contributors:
                mc.out_contributors.append(player)
                #log.info(f"{player.name} contributed to the out!")
    
    if last_thrower is not NO_PLAYER:
        log.info(f"{last_thrower.name} threw the ball for the out!")
        last_thrower.stats.fielding.throwouts += 1
        if last_thrower not in mc.out_contributors:
            mc.out_contributors.append(last_thrower)
            #log.info(f"{last_thrower.name} contributed to the out!")

    if out_runner is mc.batter:
        if landing_status == BallLandingStatus.CAUGHT_OUT:
            mc.batter.stats.batting.flyouts += 1
        elif landing_status in (BallLandingStatus.FAIR, BallLandingStatus.FAIR_FIELDED):
            mc.batter.stats.batting.ground_outs += 1

    mc.pitcher.stats.pitching.outs_pitched += 1
    mc.baserunners[out_runner_id.value] = NO_PLAYER
    inher_runner, old_pitcher = next((left_runner for left_runner in mc.inherited_baserunners if out_runner in left_runner), (None, mc.pitcher))
    if inher_runner is not None:
        mc.inherited_baserunners.remove((inher_runner, old_pitcher))
        print(f"({inher_runner.name}, {old_pitcher.name}) removed from inherited runners list")


def _record_score_change(game: Game, mc: MatchContext, sacrifice_possible: bool):
    """Records RBIs and earned runs if the offense team's score increased."""
    score_change = game.offense_team.score.value - mc.offense_team_score
    if score_change <= 0:
        return

    game.runs_this_inning += score_change
    
    if mc.ball_hit_flag:
        mc.batter.stats.batting.rbi += score_change
        log.info(f"{mc.batter.name} drove in {score_change} run(s)! That's {mc.batter.stats.batting.rbi} RBIs!")
    
    if sacrifice_possible:
        if not mc.sacrifice_flag:
            mc.batter.stats.batting.sac_flys += 1
        mc.sacrifice_flag = True
        log.info(f"{mc.batter.name} recorded a sacrifice fly!")
        mc.at_bat_eligible = False
        
    for i in range(0, score_change):
        game.this_pitch.num_bases_ran.refresh()
        if game.this_pitch.num_bases_ran.value == 4 and mc.batter not in mc.runners_scored_this_pitch:
            scorer = mc.batter
        else:
            scorer = next((runner for runner in reversed(mc.baserunners) if runner is not NO_PLAYER and runner not in mc.runners_scored_this_pitch), NO_PLAYER)
            
        if scorer is not NO_PLAYER:
            mc.runners_scored_this_pitch.append(scorer)
            log.info(f"{scorer.name} scored!")
            scorer.stats.running.runs += 1
            log.debug(f"{scorer.name} has now scored {scorer.stats.running.runs} runs.")
            if scorer.base is not None:
                mc.baserunners[scorer.base.base_num] = NO_PLAYER
        else:
            log.warning("Failed to identify scorer on score change.")
    
    inher_runner, pitcher = next((left_runner for left_runner in mc.inherited_baserunners if scorer in left_runner), (None, mc.pitcher))

    if pitcher != mc.pitcher:
        log.info(f"{mc.pitcher.name} inherited this runner from {pitcher.name}. {pitcher.name} will be charged any earned runs.")
        mc.pitcher.stats.pitching.inherited_runs += score_change
    
    pitcher.stats.pitching.runs_allowed += score_change
    if mc.runs_earned_this_pitch and mc.runs_earned_this_inning: # Will Always be true for now, but two bools will be used for error tracking later on.
        pitcher.stats.pitching.earned_runs += score_change
        log.debug(f"{pitcher.name} was charged with {score_change} earned run(s).")
    
    if inher_runner is not None:
        mc.inherited_baserunners.remove((inher_runner, pitcher))
        print(f"({inher_runner.name}, {pitcher.name}) removed from inherited runners list")
    
    
    
        
def _award_double_or_triple_plays(game: Game, mc: MatchContext):
    if game.this_pitch.outs.value not in (2,3):
        return
    
    if game.this_pitch.outs.value == 2:
        print(f"Number of double play contributors: {len(mc.out_contributors)}")
        for player in mc.out_contributors:
            print(f"{player.name},", end=" ")
            player.stats.fielding.double_plays += 1
        print(f"were credited with a double play!")
    else:
        print(f"Number of triple play contributors: {len(mc.out_contributors)}")
        for player in mc.out_contributors:
            print(f"{player.name},", end=" ")
            player.stats.fielding.triple_plays += 1
        print(f"were credited with a triple play!!")
    



# ----------------------REPLAY FUNCTIONS-----------------------------
def check_for_replay(game: Game, mc: MatchContext) -> bool:
    """Checks if a replay is currently underway."""
    game.team1.score.refresh()
    game.team2.score.refresh()
    game.outs.refresh()
    score_value1: int = game.team1.score.value
    score_value2: int = game.team2.score.value
    outs_value: int = game.outs.value
    
    score_or_out_changed = (
        score_value1 < mc.team1_score
        or score_value2 < mc.team2_score
        or outs_value < mc.outs
    )
    #Arbitary number that I found worked well to avoid false positives without missing replays. May need to be adjusted in the future.
    num_replay_checks = 200 
    x = 0
    while x < num_replay_checks:
        game.in_replay.refresh()
        if game.in_replay.value > 0 or score_or_out_changed:
            return True
        x += 1
    return False

def replay_state(state: Field, game: Game):
    """Loop that waits for replays to finish"""
    log.info("Stat tracking paused for replay.")
    while True:
        state.refresh()
        game.in_replay.refresh()
        if (state.display in {
            "LOAD_NEXT_BATTER",
            "END_SCORE_SCREEN",
            "MID_INNING_TRANSITION",
            "RBI_CELEBRATION_CUTSCENE"
        } or state.value not in game.STATE) and game.in_replay.value == 0:
            break
    log.info("Replay ended. Resuming stat tracking.")
    
    
    
    
    
    


# ----------------------GAME STATE FUNCTIONS -----------------------------
def intro_cutscene_state(state: Field, game: Game, mc: MatchContext):
    """Stat tracking for the intro loading & cutscene states"""
    check_hook_status()
    log.info(f"{game.team1.name} vs. {game.team2.name} @ {game.time_of_day.display} {game.map.display}")
    log.info(f"The {game.offense_team.short_name} will bat first!")

    _assign_score_and_meter_fields(game)

    while state.display == "INTRO_CUTSCENE":
        state.refresh()



def batting_state(state: Field, game: Game, team1: Team, team2: Team, mc: MatchContext, last_pitch: PitchSnapshot):
    mc.is_replay = check_for_replay(game, mc)
    if mc.is_replay:
        replay_state(state, game)
        return last_pitch



    team1.hits.refresh()
    team2.hits.refresh()
    mc.team1_hits = team1.hits.value
    mc.team2_hits = team2.hits.value
    
    if mc.pitch_thrown_flag:
        mc.pitch_id += 1
        log.debug(f"New pitch detected. Pitch ID is now {mc.pitch_id}.")
        mc.pitch_thrown_flag = False
        _process_last_pitch_snapshot(game, mc, last_pitch)
    
    
    
    for runner in mc.baserunners:
        if runner.base is None:
            continue
        _check_if_steal_success(game, runner, runner.base.base_num, mc)
    
    
    print()
    print()
    mc.offense_team = game.offense_team
    mc.defense_team = game.defense_team
    mc.ball_hit_flag = False
    mc.at_bat_eligible = True
    mc.plate_appearance_completed = False
    mc.out_contributors = []
    mc.runners_scored_this_pitch = []
    mc.fielders_touched_ball_this_pitch = []
    mc.buddy_jumper = NO_PLAYER
    offense_meter = game.offense_team.meter.value
    defense_meter = game.defense_team.meter.value
    game.strikes.refresh()
    game.balls.refresh()
    game.outs.refresh()
    game.runs_this_play = 0
    game.pitches.refresh()
    mc.pitches = game.pitches.value
    
    
    game.current_batter.refresh_all()
    game.current_batter.index.refresh()
    mc.batter = game.get_current_batter()
    mc.pitcher = game.get_current_pitcher()
    mc.positions = game.def_positions.get_all_position_indexes()
    game.def_positions.refresh_all()

    game.set_def_positions()
    if not game.match_started:
        game.match_started = True

    if game.pitches.value != mc.pitches or mc.batter is not game.get_current_batter():
        mc.pitches = game.pitches.value

    if (game.pitches.value == 0 and not mc.plate_appearance_begun) or mc.pitcher is not last_pitch.pitcher or mc.batter is not last_pitch.batter:
        log.info(f"{mc.pitcher.name} vs. {mc.batter.name}")
        log.info(f"{game.outs.value} outs")
        mc.plate_appearance_begun = True

    log.debug(f"Count: {game.balls.value}-{game.strikes.value}")

    game.set_def_positions()
    game.set_baserunners()
    _fill_baserunner_list(game, mc)
    mc.steal_attempters = [(NO_PLAYER, -1)] * 3
    mc.num_baserunners = sum(x is not NO_PLAYER for x in mc.baserunners)

    if mc.baserunners != last_pitch.baserunners:
        if mc.baserunners[1] is not NO_PLAYER:
            log.debug(f"{mc.baserunners[1].name} is on first.")
        if mc.baserunners[2] is not NO_PLAYER:
            log.debug(f"{mc.baserunners[2].name} is on second.")
        if mc.baserunners[3] is not NO_PLAYER:
            log.debug(f"{mc.baserunners[3].name} is on third.")

    while state.display == "BATTING":
        check_hook_status()
        _refresh_game_values(game, team1, team2)
        mc.is_replay = check_for_replay(game, mc)
        if mc.is_replay:
            replay_state(state, game)
            return last_pitch
        
        
        # If a pitch has been thrown
        if game.pitches.value > mc.pitches and not mc.pitch_thrown_flag:
            mc.pitch_thrown_flag = True
            mc.pitcher.stats.pitching.pitch_count += 1
            #print(f"Pitch #{mc.pitch_id} Thrown!")
            if mc.pitcher not in mc.pitchers_thrown_this_at_bat:
                mc.pitchers_thrown_this_at_bat.append(mc.pitcher)
            
            if not game.defense_team.starting_lineup_set:
                __set_starting_lineup(game, game.defense_team)
            
            if mc.pitcher not in mc.defense_team.pitcher_order:
                mc.defense_team.pitcher_order.append(mc.pitcher)
            
            mc.defense_lineup = game.def_positions.get_all_players_at_positions()
            __update_positions_played(game, game.defense_team, mc)
            
                
                
        mc.batter = game.get_current_batter()
        mc.pitcher = game.get_current_pitcher()
        
        _check_count_changes(game, mc.pitcher, mc.batter, mc)

        for runner in mc.baserunners:
            if runner is not NO_PLAYER and runner.base is not None:
                _check_if_steal_attempt(game, runner, runner.base.base_num, mc)
                
                
        _check_if_ball_was_hit(game, offense_meter, mc)
        _check_for_star_pitch_usage(game, defense_meter, mc)
        _check_for_star_hit_usage(game, offense_meter, mc)
        _check_for_bean_ball(game, mc)


        offense_meter = game.offense_team.meter.value
        defense_meter = game.defense_team.meter.value
        _update_match_context_values(game, team1, team2, mc)
        state.refresh()
    
    return _make_pitch_snapshot(mc)

def fielding_state(state: Field, game: Game, team1: Team, team2: Team, mc: MatchContext, last_pitch: PitchSnapshot):
    mc.is_replay = check_for_replay(game, mc)
    if mc.is_replay:
        replay_state(state, game)
        return last_pitch

    last_ball_status = BallPossessionStatus(game.ball_possession.ball_status.value)
    last_landing_status = BallLandingStatus(game.this_pitch.fair_or_foul.value)
    last_thrower: Player = NO_PLAYER
    landing_status_flag = False
    initial_landing_status = BallLandingStatus.AIRBORNE
    mc.offense_team_score = game.offense_team.score.value
    sacrifice_possible = False
    
    if not mc.ball_hit_flag and not mc.pickoff_attempt_flag and not mc.pitch_thrown_flag:
        mc.pickoff_attempt_flag = True
        log.info(f"{mc.pitcher.name} is attempting a pickoff!")
        
        
    while state.display == "FIELDING":
        check_hook_status()
        _refresh_game_values(game, team1, team2)
        ball_status = BallPossessionStatus(game.ball_possession.ball_status.value)
        landing_status = BallLandingStatus(game.this_pitch.fair_or_foul.value)
        

        landing_status_flag = _check_landing_status(landing_status, last_landing_status, mc)
        if landing_status_flag and initial_landing_status == BallLandingStatus.AIRBORNE:
                initial_landing_status = landing_status

        if mc.ball_hit_flag and landing_status in (BallLandingStatus.FAIR, BallLandingStatus.FAIR_FIELDED) and not mc.plate_appearance_completed:
            mc.plate_appearance_completed = True
            #log.info(f"{mc.batter.name}'s plate appearance completed with a hit ball.")
            
        current_ball_holder, last_ball_holder, new_thrower = _check_ball_status_change(ball_status, last_ball_status, game)
        
        if current_ball_holder is not NO_PLAYER and current_ball_holder not in mc.fielders_touched_ball_this_pitch:
            mc.fielders_touched_ball_this_pitch.append(current_ball_holder)
            
        if new_thrower is not NO_PLAYER:
            last_thrower = new_thrower
        
        buddy_jumper = __check_for_buddy_jump(game)
        if buddy_jumper is not mc.buddy_jumper:
            mc.buddy_jumper = buddy_jumper
            if buddy_jumper is not NO_PLAYER:
                log.info(f"{buddy_jumper.name} is going up for a buddy jump!")
        
        
        if mc.ball_hit_flag and landing_status == BallLandingStatus.CAUGHT_OUT:
            mc.plate_appearance_completed = True 
            if game.outs.value < 2:
                sacrifice_possible = True
        
        if game.this_pitch.outs.value > mc.outs_this_pitch:
            _record_out(game, mc, current_ball_holder, last_thrower, landing_status)

        _record_score_change(game, mc, sacrifice_possible)

        last_ball_status = ball_status
        last_landing_status = landing_status
        _update_match_context_values(game, team1, team2, mc)
        state.refresh()
    
    _award_double_or_triple_plays(game, mc)
    last_pitch.runners_score_this_pitch = mc.runners_scored_this_pitch
    last_pitch.outs_this_pitch = mc.outs_this_pitch
    last_pitch.sacrifice_flag = mc.sacrifice_flag
    last_pitch.out_contributors = mc.out_contributors
    last_pitch.bean_ball_flag = mc.bean_ball_flag
    last_pitch.sacrifice_flag = mc.sacrifice_flag
    
    return _make_pitch_snapshot(mc)
    

def mid_inning_transition_state(state: Field, game: Game, team1: Team, team2: Team, mc: MatchContext):
    check_hook_status()
    mc.is_replay = check_for_replay(game, mc)
    if mc.is_replay:
        replay_state(state, game)
        return 

    log.info(f"{team1.name} - {team1.score.value}")
    log.info(f"{team2.name} - {team2.score.value}")
    
    
    mc.inherited_baserunners = []
    log.info("Changing sides!")

    game.runs_this_inning = 0
    printed = False
    time.sleep(3)  # Give time for side change data to settle
    game.inning_half.refresh()
    mc.inning_half = "Top" if game.inning_half.value == 0 else "Bottom"
    game.set_def_positions()
    game.current_inning.refresh()
    log.info(f"Next: {mc.inning_half} of inning {game.current_inning.value}")

    while state.display == "MID_INNING_TRANSITION":
        _refresh_game_values(game, team1, team2)
        batter = game.get_current_batter()
        mc.batter_index = game.get_current_batter_index()
        on_deck = game.get_on_deck_batter()
        on_deck_index = game.get_on_deck_batter_index()
        if not printed:
            log.info(f"Upcoming batter: {batter.name}")
            log.info(f"On deck: {on_deck.name}")
            printed = True
        _update_match_context_values(game, team1, team2, mc)
        state.refresh()

    mc.inning_changing = False


def load_next_batter_state(state: Field, game: Game, mc: MatchContext, last_pitch: PitchSnapshot):
    check_hook_status()

    mc.plate_appearance_begun = False
    mc.ball_hit_flag = False
    mc.bean_ball_flag = False
    next_batter_index = game.get_on_deck_batter_index()
    game.offense_team.batting_index = next_batter_index
    mc.strikes = 0
    mc.balls = 0
    mc.pitches = 0
    game.runs_this_play = 0    
    
    
    global last_pitch_id_processed
    
    if last_pitch_id_processed < mc.pitch_id:
        last_pitch = _make_pitch_snapshot(mc)
        last_pitch_id_processed = mc.pitch_id
        for pitcher in mc.pitchers_thrown_this_at_bat:
            pitcher.stats.pitching.batters_faced += 1
        
        mc.pitchers_thrown_this_at_bat = []
        if mc.plate_appearance_completed:
            mc.batter.stats.batting.plate_appearances += 1
        else:
            log.info(f"{mc.batter.name}'s plate appearance was incomplete and will not be counted")
        if mc.at_bat_eligible:
            mc.batter.stats.batting.at_bats += 1
        else:
            log.info(f"{mc.batter.name} will not be credited with an at-bat for this appearance")
    else:
        log.debug(f"Pitch already processed.")

    while state.display == "LOAD_NEXT_BATTER":
        game.balls.refresh()
        game.strikes.refresh()
        game.pitches.refresh()
        state.refresh()

    return last_pitch

def end_score_screen_state(state: Field, team1: Team, team2: Team, mc: MatchContext, last_pitch: PitchSnapshot):
    check_hook_status()
    team1.hits.refresh()
    team2.hits.refresh()
    mc.team1_hits = team1.hits.value
    mc.team2_hits = team2.hits.value
    
    if mc.pitch_thrown_flag:
        mc.pitch_id += 1
        #log.debug(f"New pitch detected. Pitch ID is now {mc.pitch_id}.")
        mc.pitch_thrown_flag = False
        _process_last_pitch_snapshot(game, mc, last_pitch)
    
    log.info("Final Score:")
    log.info(f"{team1.name} - {team1.score.value}")
    log.info(f"{team2.name} - {team2.score.value}")

    if team1.score.value > team2.score.value:
        log.info(f"{team1.name} wins!")
    elif team2.score.value > team1.score.value:
        log.info(f"{team2.name} wins!")
    else:
        log.info("The game ended in a tie!")

    while state.display == "END_SCORE_SCREEN":
        state.refresh()


def pause_state(state: Field, game: Game, team1: Team, team2: Team, mc: MatchContext):
    check_hook_status()
    log.info("Game paused...")
    while state.display == "PAUSE":
        _refresh_game_values(game, team1, team2)
        _update_match_context_values(game, team1, team2, mc)
        state.refresh()


def end_stat_screen_state(state: Field, team1: Team, team2: Team, mc: MatchContext):
    check_hook_status()
    output_stats_to_excel(game)
    mc.stats_outputted = True
    while state.display == "END_STAT_SCREEN":
        state.refresh()


def hr_base_celebration_state(state: Field, mc: MatchContext, last_pitch: PitchSnapshot):
    check_hook_status()

    mc.home_run_flag = True
    
    if mc.runs_this_pitch == 1:
        log.info(f"{mc.batter.name} hits a solo homer off of {mc.pitcher.name}!")
    elif mc.runs_this_pitch == 2:
        log.info(f"{mc.batter.name} hits a two-run homer off of {mc.pitcher.name}!")
    elif mc.runs_this_pitch == 3:
        log.info(f"{mc.batter.name} hits a three-run homer off of {mc.pitcher.name}!")
    else:
        log.info(f"{mc.batter.name} hits a grand slam off of {mc.pitcher.name}!")
    
    mc.batter.stats.batting.home_runs += 1
    mc.pitcher.stats.pitching.home_runs_allowed += 1
    while state.display == "HR_BASE_CELEBRATION" or state.display == "HR_HOMEIN_CELEBRATION":
        _refresh_game_values(game, team1, team2)
        _record_score_change(game, mc, False)
        _update_match_context_values(game, team1, team2, mc)
        state.refresh()
    
    return _make_pitch_snapshot(mc)

def change_lineup_state(state: Field, game: Game, team1: Team, team2: Team, mc: MatchContext):
    check_hook_status()
    print()
    print(f"{game.defense_team.short_name} are changing their defense...")
    
    current_pitcher = mc.pitcher
    while state.display == "CHANGE_LINEUP":
        _refresh_game_values(game, team1, team2)
        _update_match_context_values(game, team1, team2, mc)
        state.refresh()

def pre_pitch_cutscene_state(state: Field, game: Game, team1: Team, team2: Team, mc: MatchContext):
    check_hook_status()
    game.last_state.refresh()
    
    #There are several pre-pitch cutscenes (tired pitcher, rbi chance), but the only time anything needs to be done is when you are coming from the change-lineup screen (new pitcher cutscene)
    if game.last_state.display == "CHANGE_LINEUP":

        game.def_positions.refresh_all()
        game.set_def_positions()

        current_lineup = mc.defense_lineup
        new_lineup = game.def_positions.get_all_players_at_positions()
        last_pitcher = mc.pitcher
        new_pitcher = mc.pitcher
        for player in game.defense_team.players:
            curr_pos = current_lineup.get(player)
            new_pos = new_lineup.get(player)
            if new_pos == "P":
                new_pitcher = player
            if new_pos != curr_pos:
                log.info(f"{player.name} was moved to {new_pos}.")
        
        if new_pitcher != last_pitcher:
            log.debug(f"PITCHER CHANGE!")
            _fill_baserunner_list(game, mc)
            for runner in mc.baserunners:
                if runner is not NO_PLAYER:
                    if not any(runner in inher_runner for inher_runner in mc.inherited_baserunners):  
                        runner_left_on_base_entry = (runner, mc.pitcher)
                        mc.inherited_baserunners.append(runner_left_on_base_entry)
                        #log.info(f"{last_pitcher.name} left {runner.name} on base when they left.")
                    

    while state.display == "PRE_PITCH_CUTSCENE":
        state.refresh()
                


# ------Main Code-------
if __name__ == "__main__":
    TEAM1_ADDRESSES = {
        "base_address_list": [0x8131B4B9 + i * 0x8E for i in range(9)],
        "stamina_address_list": [0x900D61A0 + i * 0x20 for i in range(9)],
        "branding_address": 0x811f76AC,
        "player_type_address": 0x811f76b0,
        "batting_fielding_address": 0x900d5c22,
        "team_number": 1,
        "pitching_index_address": 0x900d5ced
    }

    TEAM2_ADDRESSES = {
        "base_address_list": [0x8131B9B7 + i * 0x8E for i in range(9)],
        "stamina_address_list": [0x900D62C0 + i * 0x20 for i in range(9)],
        "branding_address": 0x811f76AD,
        "player_type_address": 0x811f76b1,
        "batting_fielding_address": 0x900d5c23,
        "team_number": 2,
        "pitching_index_address": 0x900d5cc5
    }

    main_loop_on = True
    max_hooks_attempts = (max_wait_for_hook_mins * 60) // tick_wait_time if tick_wait_time != 0 else max_wait_for_hook_mins * 60
    hook_attempts = 0

    while main_loop_on:
        match_start = None
        while hook_attempts < max_hooks_attempts:
            is_hooked = hook_dolphin()
            if is_hooked:
                break

            if max_hooks_attempts - hook_attempts <= 1:
                log.critical("Stat tracker failed to hook to Dolphin process.")
                time.sleep(3)
                sys.exit()

        while True:
            match_start = detect_match_start()
            if match_start is not None:
                break

        if match_start == 2:
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
            log.info("Match is starting now!")
            time.sleep(1)  # Give time for the game to fully load

        log.info("Initializing game, team, and player data...")

        team1 = Team(**TEAM1_ADDRESSES)
        team2 = Team(**TEAM2_ADDRESSES)
        game = Game(team1, team2)
        state = game.game_state
        match_context = MatchContext()
        last_pitch : PitchSnapshot = PitchSnapshot(
            0, 0, 0, 0,
            NO_PLAYER, NO_PLAYER,
            [NO_PLAYER] * 3, [], [], [],
            NO_TEAM, NO_TEAM,
            0, 0, 0,
            0, 0,
            False, False, False, False, 0
        )
        if match_start == 2:
            _assign_score_and_meter_fields(game)

        while game.being_played:
            match state.display:
                case "BATTING":
                    last_pitch = batting_state(state, game, team1, team2, match_context, last_pitch)
                case "FIELDING":
                    last_pitch = fielding_state(state, game, team1, team2, match_context, last_pitch)
                case "MID_INNING_TRANSITION":
                    mid_inning_transition_state(state, game, team1, team2, match_context)
                case "INTRO_CUTSCENE":
                    intro_cutscene_state(state, game, match_context)
                case "LOAD_NEXT_BATTER":
                    last_pitch = load_next_batter_state(state, game, match_context, last_pitch)
                case "END_SCORE_SCREEN":
                    end_score_screen_state(state, team1, team2, match_context, last_pitch)
                case "PAUSE":
                    pause_state(state, game, team1, team2, match_context)
                case "END_STAT_SCREEN":
                    end_stat_screen_state(state, team1, team2, match_context)
                case "HR_BASE_CELEBRATION":
                    last_pitch = hr_base_celebration_state(state, match_context, last_pitch)
                case "CHANGE_LINEUP":
                    change_lineup_state(state, game, team1, team2, match_context)
                case "PRE_PITCH_CUTSCENE":
                    pre_pitch_cutscene_state(state, game, team1, team2, match_context)
                case "REMATCH":
                    state.refresh()
                case _ if state.value not in game.STATE:
                    log.error(f"State value is out of range: {state.value}")
                    game.being_played = False
                case _:
                    state.refresh()

        log.info("Game ended. Printing stats:")
        if not match_context.stats_outputted:
            output_stats_to_excel(game)
            match_context.stats_outputted = True

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