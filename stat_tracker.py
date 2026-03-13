import time
import sys
import logging
import dolphin_memory_engine as dme
from dolphin_mem import Data, read_data, write_data
from MemoryHandling.sluggers_data import Field, Player, Team, Game, NO_PLAYER, NO_TEAM
from openpyxl import Workbook, load_workbook
from enum import Enum

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)


class BallPossessionStatus(Enum):
    UNFIELDED = 0
    HELD = 1
    THROWN = 2
    FREE = 3


class BallLandingStatus(Enum):
    FOUL = -1
    AIRBORNE = 0
    FAIR = 1
    FAIR_FIELDED = 2
    CAUGHT_OUT = 3



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
        self.batter: Player = NO_PLAYER
        self.pitcher: Player = NO_PLAYER
        self.positions: dict = {}
        self.inning_half: str = "Top"
        self.num_baserunners: int = 0
        self.ball_status: BallPossessionStatus = BallPossessionStatus.UNFIELDED
        self.ball_holder: Player = NO_PLAYER
        self.runs_this_pitch: int = 0
        self.outs_this_pitch: int = 0
        self.baserunners: list[Player] = [NO_PLAYER] * 4
        self.steal_attempters: list[tuple[Player, int]] = []
        self.runners_scored_this_pitch: list[Player] = []
        self.offense_team_score: int = 0
        self.num_bases_ran: int = 0
        self.out_contributors: list[Player] = []
        self.ball_is_being_held: bool = False
        self.is_replay: bool = False
        self.inning_changing: bool = False
        self.stats_printed: bool = False
        self.ball_hit_flag: bool = False
        self.plate_appearance_begun: bool = False
        self.plate_appearance_completed: bool = False
        self.runs_earned_this_pitch: bool = True
        self.runs_earned_this_inning: bool = True
        self.at_bat_eligible: bool = True
        self.home_run_flag: bool = False
        self.sacrifice_flag: bool = False

class PitchSnapshot:
    def __init__(self, balls: int, strikes: int, outs: int = 0, batter: Player = NO_PLAYER, pitcher: Player = NO_PLAYER,
                 baserunners: list[Player] = [], steal_attempters: list[tuple[Player, int]] = [],
                 runners_score_this_pitch: list[Player] = [], out_contributors: list[Player] = [],
                 num_bases_ran: int = 0, offense_hits: int = 0, offense_score: int = 0,
                 runs_this_pitch: int = 0, outs_this_pitch: int = 0, ball_hit_flag: bool = False,
                 home_run_flag: bool = False, sacrifice_flag: bool = False):
        self.balls = balls
        self.strikes = strikes
        self.outs = outs
        self.batter = batter
        self.pitcher = pitcher
        self.baserunners = baserunners if baserunners is not None else []
        self.steal_attempters = steal_attempters if steal_attempters is not None else []
        self.runners_score_this_pitch = runners_score_this_pitch if runners_score_this_pitch is not None else []
        self.out_contributors = out_contributors if out_contributors is not None else []
        self.num_bases_ran = num_bases_ran
        self.offense_hits = offense_hits
        self.offense_score = offense_score
        self.runs_this_pitch = runs_this_pitch
        self.outs_this_pitch = outs_this_pitch
        self.ball_hit_flag = ball_hit_flag
        self.home_run_flag = home_run_flag
        self.sacrifice_flag = sacrifice_flag
    
    
    

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
    team1.hits.refresh()
    team2.hits.refresh()
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

def _update_compare_values(game: Game, team1: Team, team2: Team, mc: MatchContext):
    mc.team1_score = team1.score.value
    mc.team2_score = team2.score.value
    mc.team1_hits = team1.hits.value
    mc.team2_hits = team2.hits.value
    mc.outs = game.outs.value
    mc.balls = game.balls.value
    mc.strikes = game.strikes.value
    mc.ball_status = game.ball_possession.ball_status.value
    mc.outs_this_pitch = game.this_pitch.outs.value
    mc.runs_this_pitch = game.this_pitch.runs.value
    mc.ball_holder = game.get_current_ball_holder()
    mc.offense_team_score = game.offense_team.score.value
    mc.ball_is_being_held = game.ball_possession.current_ball_holder_index.value >= 0

def _make_pitch_snapshot(mc: MatchContext) -> PitchSnapshot:
    return PitchSnapshot(
        balls=mc.balls,
        strikes=mc.strikes,
        outs=mc.outs,
        batter=mc.batter,
        pitcher=mc.pitcher,
        baserunners=mc.baserunners.copy(),
        steal_attempters=mc.steal_attempters.copy(),
        runners_score_this_pitch=mc.runners_scored_this_pitch.copy(),
        out_contributors=mc.out_contributors.copy(),
        num_bases_ran=mc.num_bases_ran,
        offense_hits=mc.team1_hits if mc.offense_team is game.team1 else mc.team2_hits,
        offense_score=mc.team1_score if mc.offense_team is game.team1 else mc.team2_score,
        runs_this_pitch=mc.runs_this_pitch,
        outs_this_pitch=mc.outs_this_pitch,
        ball_hit_flag=mc.ball_hit_flag,
        home_run_flag=mc.home_run_flag,
        sacrifice_flag=mc.sacrifice_flag
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

def print_stats(team: Team):
    """Prints out a list of each character's stats on a team (will be replaced by sheet creation)"""
    log.info(f"{team.name} Stats:")
    for player in team.players:
        s = player.stats
        log.info(f"\n{player.name}'s Stats:")
        log.info("BATTING STATS:")
        log.info("---------------------")
        log.info(f"At Bats: {s.batting.at_bats}")
        log.info(f"RBI: {s.batting.rbi}")
        log.info(f"Home Runs: {s.batting.home_runs}")
        log.info(f"Strikeouts: {s.batting.strikeouts}")
        log.info(f"Walks: {s.batting.walks}")
        log.info(f"Star Hits: {s.batting.star_hits}")
        log.info(f"Hit By Pitch: {s.batting.hit_by_pitch}")
        log.info(f"Singles: {s.batting.singles}")
        log.info(f"Doubles: {s.batting.doubles}")
        log.info(f"Triples: {s.batting.triples}")
        log.info(f"Fly Outs: {s.batting.flyouts}")
        log.info(f"Ground Outs: {s.batting.ground_outs}")
        log.info(f"Batting Average: {s.batting.batting_average:.3f}")
        log.info(f"Slugging Percentage: {s.batting.slugging_percentage:.3f}")
        log.info(f"Total Bases: {s.batting.total_bases}")
        log.info(f"On Base Percentage: {s.batting.on_base_percentage:.3f}")
        log.info(f"On Base Plus Slugging: {s.batting.on_base_slugging:.3f}")

        log.info("FIELDING STATS:")
        log.info("---------------------")
        log.info(f"Putouts: {s.fielding.putouts}")
        log.info(f"Assists: {s.fielding.assists}")
        log.info(f"Errors: {s.fielding.errors}")
        log.info(f"Double Plays: {s.fielding.double_plays}")
        log.info(f"Triple Plays: {s.fielding.triple_plays}")
        log.info(f"Close Plays Won: {s.fielding.close_plays_won}")
        log.info(f"Close Plays Lost: {s.fielding.close_plays_lost}")
        log.info(f"Fielding Chance: {s.fielding.fielding_chances}")

        log.info("PITCHING STATS:")
        log.info("---------------------")
        log.info(f"Innings Pitched: {s.pitching.innings_pitched:.1f}")
        log.info(f"Earned Runs: {s.pitching.earned_runs}")
        log.info(f"Strikeouts: {s.pitching.strikeouts}")
        log.info(f"Star Pitches: {s.pitching.star_pitches}")
        log.info(f"Batters Faced: {s.pitching.batters_faced}")
        log.info(f"Home Runs Allowed: {s.pitching.home_runs_allowed}")
        log.info(f"Hits Allowed: {s.pitching.hits_allowed}")
        log.info(f"Balls: {s.pitching.balls}")
        log.info(f"Strikes: {s.pitching.strikes}")
        log.info(f"Walks: {s.pitching.walks}")
        log.info(f"Hit By Pitch: {s.pitching.hit_by_pitch}")
        log.info(f"ERA: {s.pitching.era:.2f}")

        log.info("Running Stats:")
        log.info("---------------------")
        log.info(f"Steals: {s.running.steals}")
        log.info(f"Caught Stealing: {s.running.caught_stealing}")
        log.info(f"Steal Attempts: {s.running.steal_attempts}")
        log.info(f"Close Plays Won: {s.running.close_plays_won}")
        log.info(f"Close Plays Lost: {s.running.close_plays_lost}")
        log.info(f"Runs: {s.running.runs}")


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

def _check_if_hit_was_recorded(game: Game, team1: Team, team2: Team, mc: MatchContext):
    game.offense_team.hits.refresh()
    hits = mc.team1_hits if game.offense_team is team1 else mc.team2_hits

    if game.offense_team.hits.value > hits:
        log.debug(f"Hit {game.offense_team.hits.value} recorded for the {game.offense_team.name}")

def _fill_baserunner_list(game: Game, mc: MatchContext):
    r = game.baserunners
    runnerlist = [r.first_base, r.second_base, r.third_base]

    for runner in runnerlist:
        if runner.player is not NO_PLAYER and runner.base_num is not None:
            mc.baserunners[runner.base_num] = runner.player
        else:
            mc.baserunners[runner.base_num] = NO_PLAYER

# ---------------------- BATTING STATE HELPER FUNCTIONS-----------------------------
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
    
    if strike_happened and game.strikes.value == 3:
        mc.at_bat_eligible = False
        mc.pitcher.stats.pitching.strikeouts += 1
        mc.batter.stats.batting.strikeouts += 1
        log.info(f"{mc.pitcher.name} struck out {mc.batter.name}!")


def _check_if_steal_attempt(game: Game, runner: Player, base_num: int, mc: MatchContext):
    """Checks if a runner is attempting to steal a base"""
    if runner.base is None:
        return
    
    runner.base.refresh_all()
    if runner.base.is_stealing.value != 0:
        steal_entry = (runner, runner.base.base_num)
        if steal_entry not in mc.steal_attempters:
            mc.steal_attempters.append(steal_entry)
            #runner.stats.running.steal_attempts += 1
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
        
        if player is runner and runner.base.base_num > steal_base_num:
            log.info(f"{runner.name} stole a base!")
            runner.stats.running.steals += 1
        
def _check_if_ball_was_hit(game: Game, offense_meter: int, mc: MatchContext):
    """Checks if the batter hit the ball, adds the batters player objects to baserunners list
        and clears steal attempters list"""
    if game.ball_was_hit.value == 1 and not mc.ball_hit_flag:
        mc.baserunners[0] = mc.batter
        mc.num_baserunners = sum(x is not NO_PLAYER for x in mc.baserunners)
        log.debug(f"{mc.baserunners[0].name} - {mc.num_baserunners}")
        mc.steal_attempters = []
        mc.ball_hit_flag = True
        _check_for_star_hit_usage(game, offense_meter, mc)

        
            

def _check_for_star_hit_usage(game: Game, offense_meter: int, mc: MatchContext):
    """Checks if the batter hit the ball with a star hit and awards the stat"""
    sc = game.star_costs
    if game.offense_team.meter.value - offense_meter in (sc.captain_star_cost.value, sc.non_main_captain_star_cost.value, sc.regular_star_cost.value):
        mc.batter.stats.batting.star_hits += 1
        log.info(f"{mc.batter.name} used a star hit!")

def _check_for_star_pitch_usage(game: Game, defense_meter: int, mc: MatchContext):
    """Checks if the pitcher threw a star hit and awards the stat"""
    sc = game.star_costs
    if game.defense_team.meter.value - defense_meter in (sc.captain_star_cost.value, sc.non_main_captain_star_cost.value, sc.regular_star_cost.value):
        mc.pitcher.stats.pitching.star_pitches += 1
        log.info(f"{mc.pitcher.name} used a star pitch!")

def _process_last_pitch_snapshot(game: Game, mc: MatchContext):
    pass

    
# ---------------------- FIELDING STATE HELPER FUNCTIONS-----------------------------   
def _check_ball_status_change(ball_status: BallPossessionStatus, last_ball_status: BallPossessionStatus, game: Game) -> tuple[Player, Player, Player]:
    """Checks if the ball status changed and logs accordingly.
    Returns (last_holder, last_thrower)"""
    new_thrower = NO_PLAYER
    current_ball_holder = game.get_current_ball_holder()
    last_ball_holder = game.get_last_player_to_touch_ball()
   
    if last_ball_status != ball_status:
        last_holder = game.get_last_player_to_touch_ball()
        if ball_status == BallPossessionStatus.HELD:
            while current_ball_holder == NO_PLAYER:
                game.ball_possession.refresh_all()
                current_ball_holder = game.get_current_ball_holder()
            log.debug(f"{current_ball_holder.name} got the ball.")
        elif ball_status == BallPossessionStatus.THROWN:
            print(f"LAST BALL HOLDER = {last_ball_holder.name}")
            new_thrower = last_ball_holder
            if new_thrower is not NO_PLAYER:
                log.debug(f"{new_thrower.name} threw the ball.")
        elif ball_status == BallPossessionStatus.FREE:
            print(f"BLUNDER OCCURRED!!!")
            new_thrower = NO_PLAYER
                
    return current_ball_holder, last_ball_holder, new_thrower


def _check_landing_status(landing_status: BallLandingStatus, mc: MatchContext) -> bool:
    """Logs the landing status of the ball and records foul balls.
    Returns True if landing status was newly recorded, False if already recorded or still airborne."""
    if landing_status == BallLandingStatus.AIRBORNE:
        return False
    
    match landing_status:
        case BallLandingStatus.FOUL:
            log.info("Foul ball!")
            mc.batter.stats.batting.foul_balls += 1
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

    out_runner_id = game.this_pitch.get_out_runner_by_num(game.this_pitch.outs.value)
    out_runner = mc.baserunners[out_runner_id.value]

    # Person holding ball occasionally updates after the out is recorded.
    # Keep refreshing until it does update.
    while last_holder is NO_PLAYER or out_runner is NO_PLAYER:
        last_holder = game.get_current_ball_holder()
        out_runner_id.refresh()
        out_runner = mc.baserunners[out_runner_id.value]

    log.info(f"{last_holder.name} got {out_runner.name} out!")

    last_holder.stats.fielding.putouts += 1

    if last_thrower is not NO_PLAYER:
        log.info(f"{last_thrower.name} assisted!")
        last_thrower.stats.fielding.assists += 1
        if last_thrower not in mc.out_contributors:
            mc.out_contributors.append(last_thrower)

    if out_runner is mc.batter:
        if landing_status == BallLandingStatus.CAUGHT_OUT:
            mc.batter.stats.batting.flyouts += 1
        elif landing_status in (BallLandingStatus.FAIR, BallLandingStatus.FAIR_FIELDED):
            mc.batter.stats.batting.ground_outs += 1

    mc.pitcher.stats.pitching.outs_pitched += 1
    mc.baserunners[out_runner_id.value] = NO_PLAYER


def _record_score_change(game: Game, mc: MatchContext):
    """Records RBIs and earned runs if the offense team's score increased."""
    score_change = game.offense_team.score.value - mc.offense_team_score
    if score_change <= 0:
        return

    game.runs_this_inning += score_change
    mc.batter.stats.batting.rbi += score_change
    log.info(f"{mc.batter.name} drove in {score_change} run(s)! That's {mc.batter.stats.batting.rbi} RBIs!")
    
    if mc.runs_earned_this_pitch and mc.runs_earned_this_inning:
        mc.pitcher.stats.pitching.earned_runs += score_change
        log.debug(f"{mc.pitcher.name} was charged with {score_change} earned run(s).")
    
    for i in range(0, score_change):
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
        
def _award_double_or_triple_plays(game: Game, mc: MatchContext):
    if game.this_pitch.outs.value not in (2,3):
        return
    
    if game.this_pitch.outs.value == 2:
        for player in mc.out_contributors:
            print(f"{player.name},", end="")
            player.stats.fielding.double_plays += 1
        log.info(f"were awarded with a double play!")
    else:
        for player in mc.out_contributors:
            print(f"{player.name},", end="")
            player.stats.fielding.triple_plays += 1
        log.info(f"were awarded with a triple play!!")
    



# ----------------------REPLAY FUNCTIONS-----------------------------
def check_for_replay(game: Game):
    """Checks if a replay is currently underway."""
    game.in_replay.refresh()
    return game.in_replay.value > 0

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
    mc.is_replay = check_for_replay(game)
    if mc.is_replay:
        replay_state(state, game)
        return

    _check_if_hit_was_recorded(game, team1, team2, mc)
    
    for runner in mc.baserunners:
        if runner.base is None:
            continue
        _check_if_steal_success(game, runner, runner.base.base_num, mc)
    
    mc.offense_team = game.offense_team
    mc.defense_team = game.defense_team
    mc.ball_hit_flag = False
    mc.at_bat_eligible = True
    mc.out_contributors = []
    mc.runners_scored_this_pitch = []
    offense_meter = game.offense_team.meter.value
    defense_meter = game.defense_team.meter.value
    game.strikes.refresh()
    game.balls.refresh()
    game.outs.refresh()
    game.runs_this_play = 0

    game.current_batter.refresh_all()
    game.current_batter.index.refresh()
    mc.batter = game.get_current_batter()
    mc.pitcher = game.get_current_pitcher()
    mc.positions = game.positions.get_all_position_indexes()
    game.positions.refresh_all()

    game.set_positions()
    if not game.match_started:
        game.match_started = True

    if game.pitches.value != mc.pitches or mc.batter is not game.get_current_batter():
        mc.pitches = game.pitches.value

    game.pitches.refresh()

    if game.pitches.value == 0 and not mc.plate_appearance_begun:
        log.info(f"{mc.pitcher.name} vs. {mc.batter.name}")
        log.info(f"{game.outs.value} outs")
        mc.plate_appearance_begun = True

    log.debug(f"Count: {game.balls.value}-{game.strikes.value}")

    game.set_positions()
    game.set_baserunners()
    _fill_baserunner_list(game, mc)
    mc.steal_attempters = [(NO_PLAYER, -1)] * 3
    mc.num_baserunners = sum(x is not NO_PLAYER for x in mc.baserunners)

    if mc.baserunners[1] is not NO_PLAYER:
        log.debug(f"{mc.baserunners[1].name} is on first.")
    if mc.baserunners[2] is not NO_PLAYER:
        log.debug(f"{mc.baserunners[2].name} is on second.")
    if mc.baserunners[3] is not NO_PLAYER:
        log.debug(f"{mc.baserunners[3].name} is on third.")

    while state.display == "BATTING":
        check_hook_status()
        _refresh_game_values(game, team1, team2)
        mc.is_replay = check_for_replay(game)
        if mc.is_replay:
            replay_state(state, game)
            return

        mc.batter = game.get_current_batter()
        mc.pitcher = game.get_current_pitcher()
        
        _check_count_changes(game, mc.pitcher, mc.batter, mc)

        for runner in mc.baserunners:
            if runner is not NO_PLAYER and runner.base is not None:
                _check_if_steal_attempt(game, runner, runner.base.base_num, mc)

        _check_for_star_pitch_usage(game, defense_meter, mc)
        _check_if_ball_was_hit(game, offense_meter, mc)

        offense_meter = game.offense_team.meter.value
        defense_meter = game.defense_team.meter.value
        _update_compare_values(game, team1, team2, mc)
        state.refresh()
    
    last_pitch = _make_pitch_snapshot(mc)

def fielding_state(state: Field, game: Game, team1: Team, team2: Team, mc: MatchContext, last_pitch: PitchSnapshot):
    mc.is_replay = check_for_replay(game)
    if mc.is_replay:
        replay_state(state, game)
        return

    last_ball_status = BallPossessionStatus(game.ball_possession.ball_status.value)
    last_thrower: Player = NO_PLAYER
    landing_status_flag = False
    mc.offense_team_score = game.offense_team.score.value

    while state.display == "FIELDING":
        check_hook_status()
        _refresh_game_values(game, team1, team2)
        ball_status = BallPossessionStatus(game.ball_possession.ball_status.value)
        landing_status = BallLandingStatus(game.this_pitch.fair_or_foul.value)
        mc.is_replay = check_for_replay(game)

        current_ball_holder, last_ball_holder, new_thrower = _check_ball_status_change(ball_status, last_ball_status, game)
        
        if new_thrower is not NO_PLAYER:
            last_thrower = new_thrower
        

        if not landing_status_flag:
            landing_status_flag = _check_landing_status(landing_status, mc)

        if game.this_pitch.outs.value > mc.outs_this_pitch:
            _record_out(game, mc, current_ball_holder, last_thrower, landing_status)

        _record_score_change(game, mc)

        last_ball_status = ball_status
        _update_compare_values(game, team1, team2, mc)
        state.refresh()
    
    _award_double_or_triple_plays(game, mc)
    last_pitch.runners_score_this_pitch = mc.runners_scored_this_pitch
    last_pitch.outs_this_pitch = mc.outs_this_pitch
    last_pitch.sacrifice_flag = mc.sacrifice_flag
    last_pitch.out_contributors = mc.out_contributors
    
    
    

    

def mid_inning_transition_state(state: Field, game: Game, team1: Team, team2: Team, mc: MatchContext):
    check_hook_status()
    mc.is_replay = check_for_replay(game)
    if mc.is_replay:
        replay_state(state, game)
        return

    log.info(f"{team1.name} - {team1.score.value}")
    log.info(f"{team2.name} - {team2.score.value}")
    
    
    log.info("Changing sides!")

    game.runs_this_inning = 0
    printed = False
    time.sleep(3)  # Give time for side change data to settle
    game.inning_half.refresh()
    mc.inning_half = "Top" if game.inning_half.value == 0 else "Bottom"
    game.set_positions()
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
        _update_compare_values(game, team1, team2, mc)
        state.refresh()

    mc.inning_changing = False


def load_next_batter_state(state: Field, game: Game, mc: MatchContext):
    check_hook_status()

    
    mc.plate_appearance_begun = False
    mc.ball_hit_flag = False
    next_batter_index = game.get_on_deck_batter_index()
    game.offense_team.batting_index = next_batter_index
    mc.strikes = 0
    mc.balls = 0
    game.runs_this_play = 0
    mc.pitcher.stats.pitching.batters_faced += 1
    mc.batter.stats.batting.plate_appearances += 1
    if mc.at_bat_eligible:
        mc.batter.stats.batting.at_bats += 1

    while state.display == "LOAD_NEXT_BATTER":
        game.balls.refresh()
        game.strikes.refresh()
        game.pitches.refresh()
        state.refresh()


def end_score_screen_state(state: Field, team1: Team, team2: Team):
    check_hook_status()
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


def pause_state(state, game, team1, team2, mc: MatchContext):
    check_hook_status()
    log.info("Game paused...")
    while state.display == "PAUSE":
        _refresh_game_values(game, team1, team2)
        _update_compare_values(game, team1, team2, mc)
        state.refresh()


def end_stat_screen_state(state, team1, team2, mc: MatchContext):
    check_hook_status()
    print_stats(team1)
    print_stats(team2)
    mc.stats_printed = True
    while state.display == "END_STAT_SCREEN":
        state.refresh()


def hr_base_celebration_state(state, mc: MatchContext, last_pitch: PitchSnapshot):
    check_hook_status()

    mc.home_run_flag = True
    log.info(f"{mc.batter.name} hits a homer off of {mc.pitcher.name}!")
    mc.batter.stats.batting.home_runs += 1
    mc.pitcher.stats.pitching.home_runs_allowed += 1

    while state.display == "HR_BASE_CELEBRATION" or state.display == "HR_HOMEIN_CELEBRATION":
        _refresh_game_values(game, team1, team2)
        _record_score_change(game, mc)
        _update_compare_values(game, team1, team2, mc)
        state.refresh()
    last_pitch = _make_pitch_snapshot(mc)

def change_lineup_state(state, game, team1, team2, mc: MatchContext):
    check_hook_status()
    log.info("Defense is changing the lineup...")

    pre_switch_pitcher = game.get_current_pitcher()
    while state.display == "CHANGE_LINEUP":
        _refresh_game_values(game, team1, team2)
        game.set_positions()
        _update_compare_values(game, team1, team2, mc)
        state.refresh()
    mc.pitcher = pre_switch_pitcher


# ------Main Code-------
if __name__ == "__main__":
    TEAM1_ADDRESSES = {
        "base_address_list": [0x8131B4B9 + i * 0x8E for i in range(9)],
        "stamina_address_list": [0x900D61A0 + i * 0x20 for i in range(9)],
        "branding_address": 0x811f76AC,
        "player_type_address": 0x811f76b0,
        "batting_fielding_address": 0x900d5c22,
        "team_number": 1,
        "pitching_index_address": 0x900d5ced,
        "hits_address": 0x900d5dcd
    }

    TEAM2_ADDRESSES = {
        "base_address_list": [0x8131B9B7 + i * 0x8E for i in range(9)],
        "stamina_address_list": [0x900D62C0 + i * 0x20 for i in range(9)],
        "branding_address": 0x811f76AD,
        "player_type_address": 0x811f76b1,
        "batting_fielding_address": 0x900d5c23,
        "team_number": 2,
        "pitching_index_address": 0x900d5cc5,
        "hits_address": 0x900d5de7
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
        last_pitch = PitchSnapshot(0,0,0,NO_PLAYER,NO_PLAYER,[NO_PLAYER]*3,[],[],[],0,False,False,False)
        if match_start == 2:
            _assign_score_and_meter_fields(game)

        while game.being_played:
            match state.display:
                case "BATTING":
                    batting_state(state, game, team1, team2, match_context, last_pitch)
                case "FIELDING":
                    fielding_state(state, game, team1, team2, match_context, last_pitch)
                case "MID_INNING_TRANSITION":
                    mid_inning_transition_state(state, game, team1, team2, match_context)
                case "INTRO_CUTSCENE":
                    intro_cutscene_state(state, game, match_context)
                case "LOAD_NEXT_BATTER":
                    load_next_batter_state(state, game, match_context)
                case "END_SCORE_SCREEN":
                    end_score_screen_state(state, team1, team2)
                case "PAUSE":
                    pause_state(state, game, team1, team2, match_context)
                case "END_STAT_SCREEN":
                    end_stat_screen_state(state, team1, team2, match_context)
                case "HR_BASE_CELEBRATION":
                    hr_base_celebration_state(state, match_context, last_pitch)
                case "CHANGE_LINEUP":
                    change_lineup_state(state, game, team1, team2, match_context)
                case "REMATCH":
                    state.refresh()
                case _ if state.value not in game.STATE:
                    log.error(f"State value is out of range: {state.value}")
                    game.being_played = False
                case _:
                    state.refresh()

        log.info("Game ended. Printing stats:")
        if not match_context.stats_printed:
            print_stats(team1)
            print_stats(team2)

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