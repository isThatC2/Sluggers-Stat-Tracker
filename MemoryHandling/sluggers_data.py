from enum import IntEnum;
from dolphin_mem import Data, read_data, write_data 
from typing import TYPE_CHECKING, Any
import math
import struct
import bisect
import binascii
# Reading & Writing Memory Helpers

TYPE_INFO = {
    "u8":  {"size": 1, "read": lambda d: int.from_bytes(read_data(d), "big"), "write": lambda d, v: write_data(d, v.to_bytes(1, "big"))},
    "u16": {"size": 2, "read": lambda d: int.from_bytes(read_data(d), "big"), "write": lambda d, v: write_data(d, v.to_bytes(2, "big"))},
    "u32": {"size": 4, "read": lambda d: int.from_bytes(read_data(d), "big"), "write": lambda d, v: write_data(d, v.to_bytes(4, "big"))},
    "f32": {
        "size": 4, 
        "read": lambda d: struct.unpack(">f", read_data(d))[0], 
        "write": lambda d, v: write_data(d, struct.pack(">f", float(v))) 
        },
    "s8": {"size": 1, "read": lambda d: int.from_bytes(read_data(d), "big", signed=True), "write": lambda d, v: write_data(d, v.to_bytes(1, "big", signed=True))},
    "s16": {"size": 2, "read": lambda d: int.from_bytes(read_data(d), "big", signed=True), "write": lambda d, v: write_data(d, v.to_bytes(2, "big", signed=True))},
    "s32": {"size": 4, "read": lambda d: int.from_bytes(read_data(d), "big", signed=True), "write": lambda d, v: write_data(d, v.to_bytes(4, "big", signed=True))},
    "fs32": {"size": 4, "read": lambda d: struct.unpack(">f", read_data(d))[0], "write": lambda d, v: write_data(d, struct.pack(">f", float(v)))}
}

class Field:
    def __init__(self, address: int, type_str: str, lookup=None, constant_value = None):
        if type_str not in TYPE_INFO:
            raise ValueError(f"Unknown Type: {type_str}")
        
        self.address = address
        self.type_info = TYPE_INFO[type_str]
        self.size = self.type_info["size"]
        self.lookup = lookup
        self.is_constant = constant_value is not None
        if self.is_constant:
            assert constant_value is not None
            self.value = constant_value
        else:
            self.value = self.type_info["read"](Data(self.address,self.size))
    
    @classmethod
    def from_constant(cls, const_value, type_str: str, lookup=None):
        return cls(address=0x0, type_str=type_str, lookup=lookup, constant_value = const_value)
        
    def refresh(self):
        """Re-Read this field from Dolphin Memory."""
        if not self.is_constant:
            self.value = self.type_info["read"](Data(self.address, self.size))
        return self.value
    
    def write(self, value):
        """Write new value to this field in Dolphin memory"""
        if not self.is_constant:
            self.type_info["write"](Data(self.address,self.size), value)
        self.value = value
    
    @property
    def display(self):
        """Return named value if lookup table exists"""
        return self.lookup.get(self.value, "Unknown") if self.lookup else self.value
    

class Refreshable:
    def refresh_all(self):
        for name in dir(self):
            if name.startswith("_"):
                continue
            attr = getattr(self, name)
            if isinstance(attr, Field):
                if not getattr(attr, "is_constant", False):
                    attr.refresh()
            elif hasattr(attr, "refresh_all"):
                attr.refresh_all()
                
class Writable:
     def refresh_all(self):
        for name in dir(self):
            if name.startswith("_"):
                continue
            attr = getattr(self, name)
            if isinstance(attr, Field):
                if not getattr(attr, "is_constant", False):
                    attr.write(attr.value)
            elif hasattr(attr,"write_all"):
                attr.write_all()
    
        
CHAR_ID_TO_NAME = {
    0x00:"Mario", 0x01:"Luigi", 0x02:"Donkey Kong", 0x03:"Diddy Kong", 0x04:"Peach",
    0x05:"Daisy", 0x06:"Yoshi", 0x07:"Baby Mario", 0x08:"Baby Luigi", 0x09:"Bowser",
    0x0A:"Wario", 0x0B:"Waluigi", 0x0C:"Koopa Troopa", 0x0D:"Red Toad", 0x0E:"Boo",
    0x0F:"Toadette", 0x10:"Shy Guy", 0x11:"Birdo", 0x12:"Monty Mole", 0x13:"Bowser Jr.",
    0x14:"Paratroopa", 0x15:"Blue Pianta", 0x16:"Red Pianta", 0x17:"Yellow Pianta",
    0x18:"Blue Noki", 0x19:"Red Noki", 0x1A:"Green Noki", 0x1B:"Hammer Bro.",
    0x1C:"Toadsworth", 0x1D:"Blue Toad", 0x1E:"Yellow Toad", 0x1F:"Green Toad",
    0x20:"Purple Toad", 0x21:"Magikoopa", 0x22:"Red Magikoopa", 0x23:"Green Magikoopa",
    0x24:"Yellow Magikoopa", 0x25:"King Boo", 0x26:"Petey Piranha", 0x27:"Dixie Kong",
    0x28:"Goomba", 0x29:"Paragoomba", 0x2A:"Red Koopa Troopa", 0x2B:"Green Paratroopa",
    0x2C:"Blue Shy Guy", 0x2D:"Yellow Shy Guy", 0x2E:"Green Shy Guy", 0x2F:"Gray Shy Guy",
    0x30:"Dry Bones", 0x31:"Green Dry Bones", 0x32:"Dark Bones", 0x33:"Blue Dry Bones",
    0x34:"Fire Bro.", 0x35:"Boomerang Bro.", 0x36:"Wiggler", 0x37:"Blooper",
    0x38:"Funky Kong", 0x39:"Tiny Kong", 0x3A:"Kritter", 0x3B:"Blue Kritter",
    0x3C:"Red Kritter", 0x3D:"Brown Kritter", 0x3E:"King K. Rool", 0x3F:"Baby Peach",
    0x40:"Baby Daisy", 0x41:"Baby DK", 0x42:"Red Yoshi", 0x43:"Blue Yoshi",
    0x44:"Yellow Yoshi", 0x45:"Light Blue Yoshi", 0x46:"Pink Yoshi",
}
        
class Player(Refreshable,Writable):
    def __init__(self, base_address, team_nummber, batting_index):
        self.id = Field(base_address, "u8", lookup=CHAR_ID_TO_NAME)
        self.name = self.id.display
        self.stats = self.Stats()
        self.team_number = team_nummber
        self.batting_index = batting_index
        self.position: Position

    class Stats:
        def __init__(self):
            batting = self.Batting()
            pitching = self.Pitching()
            fielding = self.Fielding()
            self.positions_played: list[str] = []
            
            
        class Batting:
            def __init__(self):
                self.at_bats = 0
                self.hits = 0
                self.singles = 0
                self.doubles = 0
                self.triples = 0
                self.runs = 0
                self.rbis = 0
                self.walks = 0
                self.home_runs = 0
                self.strikeouts = 0
                self.hit_by_pitch = 0
                
                    
        
            @property
            def batting_average(self):
                return self.hits / self.at_bats if self.at_bats > 0 else 0.0
            
            @property
            def on_base_percentage(self):
                plate_appearances = self.at_bats + self.walks
                return (self.hits + self.walks) / plate_appearances if plate_appearances > 0 else 0.0
            
            @property
            def on_base_slugging(self):
                return self.on_base_percentage + self.slugging_percentage
            @property
            def total_bases(self):
                return self.singles + (2 * self.doubles) + (3 * self.triples) 
            
            @property
            def slugging_percentage(self):
                return self.total_bases / self.at_bats if self.at_bats > 0 else 0.0 
                    
        
    
        class Pitching:
            def __init__(self):
                self.strikes = 0
                self.balls = 0
                self.walks = 0
                self.strikeouts = 0
                self.hits_allowed = 0
                self.home_runs_allowed = 0
                self.batters_faced = 0
                self.earned_runs = 0
                self.pitch_count = 0
                self.outs_pitched = 0
            
            @property
            def innings_pitched(self):
                return self.outs_pitched / 3
            
            @property
            def era(self):
                return (self.earned_runs / self.innings_pitched) * 9 if self.innings_pitched > 0 else 0.0
        
        class Fielding:
            def __init__(self) -> None:
                self.assists = 0
                self.putouts = 0
                self.double_plays = 0
                self.triple_plays = 0
                self.errors = 0 
                
            @property
            def fielding_chances(self):
                return self.putouts + self.assists + self.errors
            
        
            
    
class PlayerType(IntEnum):
    HUMAN_P1 = 0x00
    HUMAN_P2 = 0x01
    HUMAN_P3 = 0x02
    HUMAN_P4 = 0x03
    CPU = 0xFF
    
    @classmethod
    def from_value(cls, value):
        return cls(value) if value in cls._value2member_map_ else cls.CPU
    
class Position(Refreshable, Writable):
    def __init__(self, index_address, name, abbrev):
        self.index = Field(index_address, "u8")
        self.position_name: str = name
        self.abbrev:str = abbrev
        self.player: Player
        
        

class Team(Refreshable, Writable):
    if TYPE_CHECKING:
        # Numeric players (optional)
        Player1: "Player"
        Player2: "Player"
        Player3: "Player"
        Player4: "Player"
        Player5: "Player"
        Player6: "Player"
        Player7: "Player"
        Player8: "Player"
        Player9: "Player"

        # Named players with "safe" attribute names
        Mario: "Player"
        Luigi: "Player"
        DonkeyKong: "Player"
        DiddyKong: "Player"
        Peach: "Player"
        Daisy: "Player"
        Yoshi: "Player"
        BabyMario: "Player"
        BabyLuigi: "Player"
        Bowser: "Player"
        Wario: "Player"
        Waluigi: "Player"
        KoopaTroopa: "Player"
        RedToad: "Player"
        Boo: "Player"
        Toadette: "Player"
        ShyGuy: "Player"
        Birdo: "Player"
        MontyMole: "Player"
        BowserJr: "Player"
        Paratroopa: "Player"
        BluePianta: "Player"
        RedPianta: "Player"
        YellowPianta: "Player"
        BlueNoki: "Player"
        RedNoki: "Player"
        GreenNoki: "Player"
        HammerBro: "Player"
        Toadsworth: "Player"
        BlueToad: "Player"
        YellowToad: "Player"
        GreenToad: "Player"
        PurpleToad: "Player"
        Magikoopa: "Player"
        RedMagikoopa: "Player"
        GreenMagikoopa: "Player"
        YellowMagikoopa: "Player"
        KingBoo: "Player"
        PeteyPiranha: "Player"
        DixieKong: "Player"
        Goomba: "Player"
        Paragoomba: "Player"
        RedKoopaTroopa: "Player"
        GreenParatroopa: "Player"
        BlueShyGuy: "Player"
        YellowShyGuy: "Player"
        GreenShyGuy: "Player"
        GrayShyGuy: "Player"
        DryBones: "Player"
        GreenDryBones: "Player"
        DarkBones: "Player"
        BlueDryBones: "Player"
        FireBro: "Player"
        BoomerangBro: "Player"
        Wiggler: "Player"
        Blooper: "Player"
        FunkyKong: "Player"
        TinyKong: "Player"
        Kritter: "Player"
        BlueKritter: "Player"
        RedKritter: "Player"
        BrownKritter: "Player"
        KingKRool: "Player"
        BabyPeach: "Player"
        BabyDaisy: "Player"
        BabyDK: "Player"
        RedYoshi: "Player"
        BlueYoshi: "Player"
        YellowYoshi: "Player"
        LightBlueYoshi: "Player"
        PinkYoshi: "Player"
    def __init__(
        self, 
        base_address_list, 
        stamina_address_list, 
        branding_address, 
        player_type_address,
        batting_fielding_address,
        team_number,
        pitching_index_address
    ):
        self.players = []

        for i, base in enumerate(base_address_list):
            stamina_address = stamina_address_list[i]
            player = Player(base, team_number, i)
            self.players.append(player)
            setattr(self, f"Player{i+1}", player)

            char_name = player.name
            safe_name = (
                     char_name.replace(" ", "")
                              .replace(".", "")
                              .replace("'", "")
            )

          
            if not hasattr(self, safe_name):
                setattr(self, safe_name, player)
        self.branding = Field(branding_address, "u8", lookup=self.TEAM_BRANDING)
        self.score: Field
        self.player_type = Field(player_type_address, "u8", lookup=PlayerType)
        self.batting_or_fielding = Field(batting_fielding_address, "u8", lookup=self.OFFENSE_DEFENSE)
        self.score_by_inning = []
        self.pitching_index = Field(pitching_index_address, "u8")
        self.position_indexes = self.PositionIndexes()
        self.batting_index  = 0  # Current batter index in lineup (0-8)
        self.meter: Field
        
        
        
    def get_player_index(self,player_ref):
        if isinstance(player_ref,str):
            player_ref = getattr(self, player_ref, None)
            if player_ref is None:
                raise ValueError(f"No player named '{player_ref}' on this team")
        else:
            return player_ref.batting_index
    
    
    def write_fielding_speed(self):
        for player in self.players:
            player.attributes.write_fielding_speed()
    
    def write_baserunning_speed(self):
        for player in self.players:
            player.attributes.write_baserunning_speed()
    
    class PositionIndexes():
        def __init__(self):
            self.pitcher = 0
            self.catcher = 0
            self.first_base = 0
            self.second_base = 0
            self.third_base = 0
            self.shortstop = 0
            self.left_field = 0
            self.center_field = 0
            self.right_field = 0
        
        def as_dict(self):
            return {
                "pitcher": self.pitcher,
                "catcher": self.catcher,
                "first_base": self.first_base,
                "second_base": self.second_base,
                "third_base": self.third_base,
                "shortstop": self.shortstop,
                "left_field": self.left_field,
                "center_field": self.center_field,
                "right_field": self.right_field,
            }
        
    TEAM_BRANDING = {
        0x00: "Mario Heroes",
        0x01: "Luigi Gentlemen",
        0x02: "DK Animals",
        0x03: "Diddy Red Caps",
        0x04: "Peach Dynasties",
        0x05: "Daisy Queen Bees",
        0x06: "Yoshi Islanders",
        0x07: "Bowser Flames",
        0x08: "Wario Garlics",
        0x09: "Waluigi Spitballs",
        0x0A: "Birdo Bows",
        0x0B: "Jr. Bombers"
    }
    
    OFFENSE_DEFENSE = {
        0x00: "BATTING",
        0x01: "FIELDING"
    }
        
        
class Game(Refreshable, Writable):
    def __init__(self, team1: Team, team2: Team):
        self.team1: Team = team1
        self.team2: Team = team2
        self.total_innings = Field(0x80794328, "u8")
        self.current_inning = Field(0x900D5D97, "u8")
        self.current_outs = Field(0x900D5AA9, "u8")
        self.current_pitches = Field(0x900D692C, "u8")
        self.current_balls = Field(0x900D5AA8,"u8")
        self.current_strikes = Field(0x900D5AA7, "u8")
        self.outs_this_play = Field(0x900D5E30, "u8")
        self.current_pitcher = self.CurrentPitcher()
        self.current_batter = self.CurrentBatter()
        self.game_state = Field(0x900d5c28, "u8", lookup=self.STATE)
        self.last_state = Field(0x900d5c29, "u8", lookup=self.STATE)
        self.being_played = True
        self.batter_index = Field(0x900DB5F9, "u8")
        self.item_chances = self.ItemChances()
        self.action_lines = Field(0x813206FD, "u8")
        self.positions = self.DefensePositions(self.team1, self.team2)
        self.ball_possession = self.BallPossession()
        self.buddy_jump_height = Field(0x80625BF8, "u16")
        self.super_buddy_jump_height = Field(0x80625C08, "u16")
        self.runs_this_inning = 0
        self.runs_this_play = 0
        self.rng = self.RNG()
        self.match_started = False
        self.game_timer = Field(0x900dfcfc, "u32")
        self.snap_flag = Field(0x806D1349, "u8")
        self.pause_flag = Field(0x900D5C2C, "u8")
        self.map = Field(0x811F769D, "u8",lookup=self.MAP)
        self.time_of_day = Field(0x811F769F, "u8",lookup=self.TIME_OF_DAY)
        self.first_bounce_height = Field(0x80625590 + (20 * self.map.value), "f32")
        self.later_bounce_height = Field(0x80625594 + (20 * self.map.value), "f32")
        self.first_bounce_speed = Field(0x80625598 + (20 * self.map.value), "f32")
        self.later_bounce_speed = Field(0x8062559C + (20 * self.map.value), "f32")
        self.rolling_slowdown = Field(0x806255A0 + (20 * self.map.value), "f32")
        self.batters_this_inning = Field(0x900D5E35, "u8")
        self.ball_was_hit = Field(0x900d6a94, "u8")
        self.batting_star_normal_modifier = Field(0x80797216, "s8")
        self.batting_star_special_modifier = Field(0x80797217, "s8")
        self.position_nums = {
            0: self.positions.pitcher,
            1: self.positions.catcher,
            2: self.positions.first_base,
            3: self.positions.second_base,
            4: self.positions.third_base,
            5: self.positions.shortstop,
            6: self.positions.left_field,
            7: self.positions.center_field,
            8: self.positions.right_field,
        }


    @property
    def offense_team(self):
        self.team1.batting_or_fielding.refresh()
        self.team2.batting_or_fielding.refresh()
        if self.team1.batting_or_fielding.value == 0x00:
            return self.team1
        else:
            return self.team2
        
    @property
    def defense_team(self):
        self.team1.batting_or_fielding.refresh()
        self.team2.batting_or_fielding.refresh()
        if self.team1.batting_or_fielding.value == 0x01:
            return self.team1
        else:
            return self.team2
    
    @property
    def score_difference(self):
        return self.offense_team.score.value - self.defense_team.score.value
    
    def get_current_pitcher(self) -> Player:
        index = self.current_pitcher.index
        index.refresh()
        return self.defense_team.players[index.value]
            
    
    def get_current_batter(self)-> Player:
        while True:
            Index = self.batter_index.value
            if Index >= 0 and Index <= 8:
                break
            else:
                self.batter_index.refresh()
        return self.offense_team.players[Index]

    def get_current_batter_index(self)-> int:
        while True:
            Index = self.batter_index.value
            if Index >= 0 and Index <= 8:
                break
            else:
                self.batter_index.refresh()
        return Index
    
    def get_on_deck_batter(self) -> Player:
        current_batter_index = self.get_current_batter().batting_index
        on_deck_index = current_batter_index + 1
        if on_deck_index > 8:
            on_deck_index = on_deck_index - 9     
        return self.offense_team.players[on_deck_index]
    
    def get_on_deck_batter_index(self) -> int:
        current_batter_index = self.get_current_batter().batting_index
        on_deck_index = current_batter_index + 1
        if on_deck_index > 8:
            on_deck_index = on_deck_index - 9
        
        return on_deck_index
    
    def get_player_from_index(self, team: Team, index: int) -> Player:
        if index < 0 or index > 8:
            raise ValueError(f"Invalid player index: {index}")
        return team.players[index]
    
    def get_player_position(self, team: Team, player: Player) -> str:
        position_indexes = team.position_indexes.as_dict()
        for position, index in position_indexes.items():
            if index == player.batting_index:
                return position
        return "Unknown"
    
    def get_player_at_position(self, team: Team, position: str) -> Player:
        position_indexes = team.position_indexes.as_dict()
        index = position_indexes.get(position)
        if index is None:
            raise ValueError(f"Invalid position: {position}")
        return team.players[index]
    
    def get_team_by_player(self, player: Player) -> Team:
        if player in self.team1.players:
            return self.team1
        elif player in self.team2.players:
            return self.team2
        else:
            raise ValueError("Player does not belong to either team.")
    
    def get_opposing_team(self, team: Team) -> Team:
        if team == self.team1:
            return self.team2
        elif team == self.team2:
            return self.team1
        else:
            raise ValueError("Team does not belong to this game.")
    
    def get_opposing_team_by_player(self, player: Player) -> Team:
        team = self.get_team_by_player(player)
        return self.get_opposing_team(team)
    
    def get_safe_name(self, char_name: str) -> str:
        return char_name.replace(" ", "").replace(".", "").replace("'", "")\
    
    
    def set_positions(self):
        def_players = self.defense_team.players
        off_players = self.offense_team.players
        pos = self.positions
        pos.refresh_all()
        for i in range(9):
            off_players[i].positions = None
        
        positions_list = [
            pos.pitcher, pos.catcher, pos.first_base, pos.second_base,
            pos.third_base, pos.shortstop, pos.left_field, pos.center_field,
            pos.right_field
        ]
        for position in positions_list:
            player = def_players[position.index.value]
            player.position = position
            position.player = player

    class DefensePositions(Refreshable, Writable):
        def __init__(self, team1: Team, team2: Team):
            self.team1 = team1
            self.team2 = team2
            self.pitcher = Position(0x900d9b45, "Pitcher", "P")
            self.catcher = Position(0x900d9e31, "Catcher", "C")
            self.first_base = Position(0x900da11d, "First Base", "1B")
            self.second_base = Position(0x900da409, "Second Base", "2B")
            self.third_base = Position(0x900da6f5, "Third Base", "3B")
            self.shortstop = Position(0x900da9e1, "Shortstop", "SS")
            self.left_field = Position(0x900daccd, "Left Field", "LF")
            self.center_field = Position(0x900dafb9, "Center Field", "CF")
            self.right_field = Position(0x900db2a5, "Right Field", "RF")
        
        def refresh_all(self):
            positions = [
                self.pitcher, self.catcher, self.first_base, self.second_base,
                self.third_base, self.shortstop, self.left_field, self.center_field,
                self.right_field
            ]

            for position in positions:
                position.index.refresh()

        
        
        def get_position_index(self, position_name: str) -> int:
            self.refresh_all()
            position_map = {
                "pitcher": self.pitcher,
                "catcher": self.catcher,
                "first_base": self.first_base,
                "second_base": self.second_base,
                "third_base": self.third_base,
                "shortstop": self.shortstop,
                "left_field": self.left_field,
                "center_field": self.center_field,
                "right_field": self.right_field,
            }
            position = position_map.get(position_name.lower())
            if position is None:
                raise ValueError(f"Invalid position name: {position_name}")
            return position.index.value
        
        
        def get_all_position_indexes(self) -> dict:
            self.refresh_all()
            return {
                "pitcher": self.pitcher.index.value,
                "catcher": self.catcher.index.value,
                "first_base": self.first_base.index.value,
                "second_base": self.second_base.index.value,
                "third_base": self.third_base.index.value,
                "shortstop": self.shortstop.index.value,
                "left_field": self.left_field.index.value,
                "center_field": self.center_field.index.value,
                "right_field": self.right_field.index.value,
            }
        
    class BallPossession(Refreshable,Writable):
        def __init__(self) -> None:
            self.last_to_have_ball_index = Field(0x900d5056, "u8")
            self.current_ball_holder_index = Field(0x900d66c9, "s8")
            self.ball_status = Field(0x900d953a, "u8")
    
    def get_player_having_ball(self):
        self.ball_possession.refresh_all()
        holder_index = self.ball_possession.current_ball_holder_index.value
        if holder_index >=0 and holder_index <=8:
            pos = self.position_nums.get(holder_index)
            if pos is not None:
                return pos.player
        else:
            return None
    
    def get_last_player_having_ball(self):
        self.ball_possession.refresh_all()
        last_holder_index = self.ball_possession.last_to_have_ball_index.value
        if last_holder_index >=0 and last_holder_index <=8:
            pos = self.position_nums.get(last_holder_index)
            if pos is not None:
                return pos.player
        else:
            return None
            
    class CurrentPitcher(Refreshable, Writable):
        def __init__(self):
            self.index = Field(0x900d9b45, "u8")
            self.id = Field(0x900d9b47, "u8", lookup = CHAR_ID_TO_NAME)
            self.curve_pitch_speed = Field(0x900D691C, "u16")
            self.charge_pitch_speed = Field(0x900D691E, "u16")
            self.curve = Field(0x900D6920, "u16")
            self.captain_star_pitch = Field(0x900D6929, "u8")
            self.pitch_timer = Field(0x900DFCFC, "u16")
        
        def getCurrentPitcherName(self):
            return self.id.display


    class CurrentBatter(Refreshable, Writable):
        def __init__(self):
            self.id = Field(0x900d69ef, "u8", lookup = CHAR_ID_TO_NAME)
            
            self.slap_contact_size = Field(0x900D6A64, "u16")
            self.slap_contact_size_pp = Field(0x900D6A72, "u16")
            
            self.charge_contact_size = Field(0x900D6A66, "u16")
            self.charge_contact_size_pp = Field(0x900D6A74, "u16")
            
            self.slap_hit_power = Field(0x900D6A68, "u16")
            self.slap_hit_power_pp = Field(0x900D6A6E, "u16")
            
            self.charge_hit_power = Field(0x900D6A6A, "u16")
            self.charge_hit_power_pp = Field(0x900D6A70, "u16")
            
            self.bunting = Field(0x900D6A6C, "u16")
            
        def getCurrentBatterName(self):
            return self.id.display
        
        
    
            


    class ItemGroup(Refreshable, Writable):
        """Represents a single phase (early/middle/late) of item chances."""
        def __init__(self, base_address: int):
            self.shell = Field(base_address + 0, "u8")
            self.fire = Field(base_address + 1, "u8")
            self.bomb = Field(base_address + 2, "u8")
            self.pow = Field(base_address + 3, "u8")
            self.banana = Field(base_address + 4, "u8")
            self.boo = Field(base_address + 5, "u8")


    class ItemChances(Refreshable, Writable):
        """Top-level container for all item chance groups."""
        def __init__(self):
            base_addr = 0x80630FB0
            self.early  = Game.ItemGroup(base_addr + 0)   # 0x80630FB0
            self.middle = Game.ItemGroup(base_addr + 6)   # 0x80630FB6
            self.late   = Game.ItemGroup(base_addr + 12)  # 0x80630FBC
            
        def get_current_item_group(self, current_inning: int, total_innings: int, full_game_innings: int = 9):
            """
            full_game_innings: canonical full-game length (default 9).
            late keeps its full canonical size (ceil(full_game_innings/3)).
            middle keeps its canonical size next, capped by remaining innings.
            early gets the rest (may be 0).
            """
            # canonical sizes (based on full_game_innings)
            canonical_third = math.ceil(full_game_innings / 3)
            late_size = min(canonical_third, total_innings)
            remaining_after_late = total_innings - late_size

            middle_size = min(canonical_third, remaining_after_late)
            early_size = total_innings - late_size - middle_size

            # compute start innings (1-indexed)
            late_start = total_innings - late_size + 1
            middle_start = late_start - middle_size

            if current_inning >= late_start:
                return self.late
            elif current_inning >= middle_start:
                return self.middle
            else:
                return self.early
    
    class RNG(Refreshable, Writable):
        def __init__(self):
            self.per_pitch = Field(0x806D12B7, "u8") # 0-100 rng for per-pitch calculations
            self.per_at_bat = Field(0x806D12B6, "u8") # 0-100 rng for per-at-bat calculations
                    
    
    
    TIME_OF_DAY = {
        0x00: "Daytime",
        0x01: "Nighttime"
    }
    
    MAP = {
        0x00: "Mario Stadium",
        0x01: "Bowser's Castle",
        0x02: "Wario City",
        0x03: "Yoshi Park",
        0x04: "Peach Ice Garden",
        0x05: "DK Jungle",
        0x06: "Luigi's Mansion",
        0x07: "Daisy Cruiser",
        0x08: "Bowser Jr. Playroom"
    }
    STATE= {
    0x00: "LOAD",
    0x01: "BATTING",
    0x02: "FIELDING",
    0x03: "MID_INNING_TRANSITION",
    0x04: "INTRO_LOADING",
    0x05: "INTRO_CUTSCENE",
    0x07: "CELEBRATION",
    0x08: "LOAD_NEXT_BATTER",
    0x09: "END_SCORE_SCREEN",
    0x0A: "UNKNOWN_STATE_A",
    0x0B: "PAUSE",
    0x0C: "UNKNOWN_STATE_C",
    0x0D: "CONTROLS_SCREEN",
    0x0E: "END_STAT_SCREEN",
    0x0F: "UNKNOWN_FIELDING",
    0x10: "UNKNOWN_STATE_10",
    0x11: "UNKNOWN_STATE_11",
    0x12: "UNKNOWN_STATE_12",
    0x13: "HR_HOMEIN_CELEBRATION",
    0x14: "HR_BASE_CELEBRATION",
    0x15: "RBI_CELEBRATION_CUTSCENE",
    0x16: "PRE_PITCH_CUTSCENE",
    0x17: "UNKNOWN_STATE_17",
    0x18: "UNKNOWN_STATE_18",
    0x1A: "COIN_TOSS",
    0x1B: "WIN_ANIMATION",
    0x1C: "UNKNOWN_STATE_1C",
    0x1D: "SWAP_PITCHER",
    0x1E: "CONTROLLER_TYPE_SCREEN",
    0x1F: "CHALLENGE_MODE_ITEMS",
    0x20: "RELOAD_GAME",
}
    
    
    
        
        
        
        
                                   
        
        
        
        
            
    
    
    
        
        
                          
            
    
        
