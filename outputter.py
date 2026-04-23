from openpyxl import Workbook, load_workbook
from openpyxl.utils.cell import column_index_from_string, get_column_letter
from openpyxl.styles import Alignment
from MemoryHandling.sluggers_data import Field, Player, Team, Game, NO_PLAYER, NO_TEAM
import datetime
from pathlib import Path
class Outputter:
    def __init__(self, template_filename: str, game: Game):
        self.tmp_filename = template_filename
        self.game = game
        self.output_path = Path.cwd() / "output"
        self.output_path.mkdir(exist_ok=True)
        try:
            self.template = load_workbook(self.tmp_filename)
        except FileNotFoundError:
            raise FileNotFoundError(f"Template file '{self.tmp_filename}' not found.")
    
    
    def make_output_filename(self, game: Game) -> str:
        output_name = f"{game.team1.short_name} vs {game.team2.short_name} - {datetime.datetime.now().strftime('%Y-%m-%d %H-%M-%S')}.xlsx"
        if Path(self.output_path / output_name).exists():
            output_name = f"{game.team1.short_name} vs {game.team2.short_name} - {datetime.datetime.now().strftime('%Y-%m-%d %H-%M-%S')}_{datetime.datetime.now().microsecond}.xlsx"
        return output_name
    def save(self, output_filename: str):
        self.template.save(self.output_path / output_filename)
    
    
    def output_game_info(self, game_info_sheet, game: Game):
        team1 = game.team1
        team2 = game.team2
        gi = game_info_sheet
        if game.stat_tracker_started_during_match:
            gi["L10"] = "INCOMPLETE"
        else:
            gi["L10"] = None
        
        
        gi["L11"] = team1.name
        gi["P11"] = team2.name
        
        gi["I12"] = team1.score.value
        gi["P12"] = team2.score.value
        
        gi["I13"] = team1.player_type.display
        gi["P13"] = team2.player_type.display
        
        
        gi["I14"] = f"{game.stadium.display} - {game.time_of_day.display}"
        gi["M15"] = f"Innings - {game.total_innings.value}"
        gi["M16"] = f"Mercy - {"On" if game.mercy_flag.value == 1 else "Off"}"
        gi["M17"] = f"Stars - {"On" if game.stars_flag.value == 1 else "Off"}"
        gi["M18"] = f"Items - {"On" if game.item_flag.value == 1 else "Off"}"
        
        starting_row = 20
        for i, player in enumerate(team1.players):
            gi[f"J{starting_row + i}"] = player.name
            if team1.starting_lineup_set:
                gi[f"K{starting_row + i}"] = team1.starting_lineup.get(player, "None")
            else:
                gi[f"K{starting_row + i}"] = "None"
        
        for i, player in enumerate(team2.players):
            gi[f"R{starting_row + i}"] = player.name
            if team1.starting_lineup_set:
                gi[f"Q{starting_row + i}"] = team2.starting_lineup.get(player, "N/A")
            else:
                gi[f"Q{starting_row + i}"] = "N/A"
        
        scoreboard_row = 30
        scoreboard_start_col = "K"
        scoreboard_col_idx = column_index_from_string(scoreboard_start_col)
        current_col = scoreboard_col_idx
        gi["J31"] = game.away_team.name
        gi["J32"] = game.home_team.name
        
        for i in range(game.current_inning.value):
            gi.cell(row=scoreboard_row, column=current_col).value = i + 1
            gi.cell(row=scoreboard_row + 1, column=current_col).value = game.away_team.score_by_inning[i]
            gi.cell(row=scoreboard_row + 2, column=current_col).value = game.home_team.score_by_inning[i]
            current_col += 1
        
        gi.cell(row=scoreboard_row, column=current_col).value = "Final"
        gi.cell(row=scoreboard_row, column=current_col).alignment = Alignment(horizontal='right')
        gi.cell(row=scoreboard_row + 1, column=current_col).value = game.away_team.score.value
        gi.cell(row=scoreboard_row + 2, column=current_col).value = game.home_team.score.value
        
            
            
            
        
        
        
    
    def output_player_stats_row(self, stats_sheet, player: Player, team: Team, row: int):
            stats_sheet[f"B{row}"] = player.name
            stats_sheet[f"C{row}"] = ", ".join(player.stats.positions_played)
            stats_sheet[f"D{row}"] = player.stats.batting.at_bats
            stats_sheet[f"E{row}"] = player.stats.batting.plate_appearances
            stats_sheet[f"F{row}"] = player.stats.running.runs
            stats_sheet[f"G{row}"] = player.stats.batting.hits
            stats_sheet[f"H{row}"] = player.stats.batting.rbi
            stats_sheet[f"I{row}"] = player.stats.batting.strikeouts
            stats_sheet[f"J{row}"] = player.stats.batting.walks
            stats_sheet[f"K{row}"] = player.stats.batting.hit_by_pitch
            stats_sheet[f"L{row}"] = player.stats.batting.singles
            stats_sheet[f"M{row}"] = player.stats.batting.doubles
            stats_sheet[f"N{row}"] = player.stats.batting.triples
            stats_sheet[f"O{row}"] = player.stats.batting.home_runs
            stats_sheet[f"P{row}"] = player.stats.batting.inside_the_park_home_runs
            stats_sheet[f"Q{row}"] = player.stats.batting.total_bases
            stats_sheet[f"R{row}"] = player.stats.batting.sac_flys
            stats_sheet[f"S{row}"] = player.stats.batting.star_hits
            stats_sheet[f"T{row}"] = player.stats.batting.batting_average
            stats_sheet[f"U{row}"] = player.stats.batting.on_base_percentage
            stats_sheet[f"V{row}"] = player.stats.batting.slugging_percentage
            stats_sheet[f"W{row}"] = player.stats.batting.on_base_slugging
            stats_sheet[f"X{row}"] = player.stats.running.stolen_bases
            stats_sheet[f"Y{row}"] = player.stats.running.caught_stealing
            stats_sheet[f"Z{row}"] = player.stats.running.steal_attempts
            stats_sheet[f"AA{row}"] = player.stats.fielding.putouts
            stats_sheet[f"AB{row}"] = player.stats.fielding.assists
            stats_sheet[f"AC{row}"] = player.stats.fielding.buddy_jump_outs
            stats_sheet[f"AD{row}"] = player.stats.fielding.double_plays
            stats_sheet[f"AE{row}"] = player.stats.fielding.triple_plays
            stats_sheet[f"AF{row}"] = player.stats.fielding.errors
        
    
    def output_pitching_row(self, pitching_sheet, player: Player, team: Team, row: int):
            pitching_sheet[f"A{row}"] = team.short_name
            pitching_sheet[f"B{row}"] = player.name
            pitching_sheet[f"C{row}"] = player.stats.pitching.innings_pitched
            pitching_sheet[f"D{row}"] = player.stats.pitching.batters_faced
            pitching_sheet[f"E{row}"] = player.stats.pitching.strikeouts
            pitching_sheet[f"F{row}"] = player.stats.pitching.hits_allowed
            pitching_sheet[f"G{row}"] = player.stats.pitching.runs_allowed
            pitching_sheet[f"H{row}"] = player.stats.pitching.home_runs_allowed
            pitching_sheet[f"I{row}"] = player.stats.pitching.earned_runs
            pitching_sheet[f"J{row}"] = player.stats.pitching.inherited_runs
            pitching_sheet[f"K{row}"] = player.stats.pitching.walks
            pitching_sheet[f"L{row}"] = player.stats.pitching.bean_balls
            pitching_sheet[f"M{row}"] = player.stats.pitching.star_pitches
            pitching_sheet[f"N{row}"] = player.stats.pitching.pickoffs
            
            if player.stats.pitching.era_per_7 == float('inf'):
                pitching_sheet[f"O{row}"] = "INF"
            else:
                pitching_sheet[f"O{row}"] = player.stats.pitching.era_per_7
            
            if player.stats.pitching.era_per_9 == float('inf'):
                pitching_sheet[f"P{row}"] = "INF"
            else:
                pitching_sheet[f"P{row}"] = player.stats.pitching.era_per_9
                
            if player.stats.pitching.whip == float('inf'):
                pitching_sheet[f"Q{row}"] = "INF"
            else:
                pitching_sheet[f"Q{row}"] = player.stats.pitching.whip
    
    def output_stats(self, stats_sheet, game: Game, team1: Team, team2: Team):
        stats_sheet["A2"] = team1.short_name
        stats_sheet["A12"] = team2.short_name

        for i, player in enumerate(team1.players):
            self.output_player_stats_row(stats_sheet, player, team1, i + 2)
        
        for i, player in enumerate(team2.players):
            self.output_player_stats_row(stats_sheet, player, team2, i + 12)
            
            
            
            
        
        
    
    def output_pitching(self, pitching_sheet, game: Game, team1: Team, team2: Team):
        row_num = 2
        for i, player in enumerate(team1.pitcher_order):
            if player.stats.pitching.batters_faced > 0:
                self.output_pitching_row(pitching_sheet, player, team1, row_num)
                row_num += 1
        
        for i, player in enumerate(team2.pitcher_order):
            if player.stats.pitching.batters_faced > 0:
                self.output_pitching_row(pitching_sheet, player, team2, row_num)
                row_num += 1


    def output_game(self, game: Game):
        if "Stats" not in self.template.sheetnames:
            raise ValueError("Template does not contain a 'Stats' sheet.")
        
        if "Pitching" not in self.template.sheetnames:
            raise ValueError("Template does not contain a 'Pitching' sheet.")
        
        game_info_sheet = self.template["Game Info"]
        stats_sheet = self.template["Stats"]
        pitching_sheet = self.template["Pitching"]
        
        print("Beginning output of game data to Excel...")
        self.output_game_info(game_info_sheet, game)
        self.output_stats(stats_sheet, game, game.team1, game.team2)
        self.output_pitching(pitching_sheet, game, game.team1, game.team2)
        print("Saving output file...")
        output_filename = self.make_output_filename(game)
        self.save(output_filename)
        print(f"Output saved to '{output_filename}'")
        