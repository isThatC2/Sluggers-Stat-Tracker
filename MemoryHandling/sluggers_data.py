from enum import IntEnum
from unicodedata import name;
from dolphin_mem import Data, read_data, write_data 
from typing import TYPE_CHECKING, Any
import struct
import os
import json
import sys

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
    "char": {"size": 1, "read": lambda d: read_data(d).decode("ascii"), "write": lambda d, v: write_data(d, v.encode("ascii"))}
}

class Field:
    def __init__(self, address: int, type_str: str, lookup=None, constant_value = None):
        if type_str not in TYPE_INFO:
            raise ValueError(f"Unknown Type: {type_str}")
        
        self.addr = address
        self.type_info = TYPE_INFO[type_str]
        self.size = self.type_info["size"]
        self.lookup = lookup
        self.is_constant = constant_value is not None
        if self.is_constant:
            assert constant_value is not None
            self.value = constant_value
        else:
            self.value = self.type_info["read"](Data(self.addr,self.size))
    
    @classmethod
    def from_constant(cls, const_value, type_str: str, lookup=None):
        return cls(address=0x0, type_str=type_str, lookup=lookup, constant_value = const_value)
    
    @classmethod
    def from_pointer(cls, pointer_address: int, offset: int, type_str: str, lookup=None):
        address = int.from_bytes(read_data(Data(pointer_address, 4)), "big")  # Read the pointer value
        return cls(address=address + offset, type_str=type_str, lookup=lookup)

    def refresh(self):
        """Re-Read this field from Dolphin Memory."""
        if not self.is_constant:
            self.value = self.type_info["read"](Data(self.addr, self.size))
        return self.value
    
    def write(self, value):
        """Write new value to this field in Dolphin memory"""
        if not self.is_constant:
            self.type_info["write"](Data(self.addr,self.size), value)
        self.value = value
    
    @property
    def address(self):
        return hex(self.addr)
    
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
    0x44:"Yellow Yoshi", 0x45:"Light Blue Yoshi", 0x46:"Pink Yoshi", 0x4D: "Red Mii (M)",
    0x4E: "Orange Mii (M)", 0x4F: "Yellow Mii (M)", 0x50: "Light Green Mii (M)",
    0x51: "Green Mii (M)", 0x52: "Blue Mii (M)", 0x53: "Light Blue Mii (M)",
    0x54: "Pink Mii (M)", 0x55: "Purple Mii (M)", 0x56: "Brown Mii (M)",
    0x57: "White Mii (M)", 0x58: "Black Mii (M)", 0x59: "Red Mii (F)",
    0x5A: "Orange Mii (F)", 0x5B: "Yellow Mii (F)", 0x5C: "Light Green Mii (F)",
    0x5D: "Green Mii (F)", 0x5E: "Blue Mii (F)", 0x5F: " Light Blue Mii (F)", 
    0x60: "Pink Mii (F)", 0x61: "Purple Mii (F)", 0x62: "Brown Mii (F)",
    0x63: "White Mii (F)", 0x64: "Black Mii (F)"
}
        
class Player(Refreshable,Writable):
    def __init__(self, base_address, team_number, batting_index):
        self.id = Field(base_address, "u8", lookup=CHAR_ID_TO_NAME)
        self.name = self.id.display
        self.stats = self.Stats()
        self.team_number = team_number
        self.batting_index = batting_index
        self.def_position: DefensePosition | None = None
        self.baserunner_info: Runner | None = None

    class Stats:
        def __init__(self):
            self.batting = self.Batting()
            self.pitching = self.Pitching()
            self.fielding = self.Fielding()
            self.running = self.Running()
            self.positions_played: list[str] = []
            
            
        class Batting:
            def __init__(self):
                self.at_bats = 0
                self.hits = 0
                self.singles = 0
                self.doubles = 0
                self.triples = 0
                self.star_singles = 0
                self.star_doubles = 0
                self.star_triples = 0
                self.star_homeruns = 0
                self.rbi = 0
                self.walks = 0
                self.home_runs = 0
                self.one_run_homeruns = 0
                self.two_run_homeruns = 0
                self.three_run_homeruns = 0
                self.grand_slams = 0
                self.inside_the_park_home_runs = 0
                self.strikeouts = 0
                self.hit_by_pitch = 0
                self.star_swings = 0
                self.star_hits = 0
                self.raw_stars_used = 0
                self.flyouts = 0
                self.ground_outs = 0
                self.foul_balls = 0
                self.plate_appearances = 0
                self.sac_flys = 0
                self.sac_bunts = 0
                    
        
            @property
            def batting_average(self):
                return self.hits / self.at_bats if self.at_bats > 0 else 0.0
            
            @property
            def on_base_percentage(self):
                return (self.hits + self.walks + self.hit_by_pitch) / (self.plate_appearances - self.sac_bunts) if (self.plate_appearances - self.sac_bunts) > 0 else 0.0

            @property
            def on_base_slugging(self):
                return self.on_base_percentage + self.slugging_percentage
            @property
            def total_bases(self):
                return self.singles + (2 * self.doubles) + (3 * self.triples)  + (4 * self.home_runs)
            
            @property
            def slugging_percentage(self):
                return self.total_bases / self.at_bats if self.at_bats > 0 else 0.0 
                    
            @property
            def stars_used(self):
                return self.raw_stars_used / 50.0
            
            @property
            def total_star_bases(self):
                return self.star_singles + (2 * self.star_doubles) + (3 * self.star_triples) + (4 * self.star_homeruns)
            
            @property
            def star_slugging_percentage(self):
                if self.stars_used == 0:
                    return None
                
                return self.total_star_bases / self.stars_used
        class Pitching:
            def __init__(self):
                self.strikes = 0
                self.balls = 0
                self.walks = 0
                self.bean_balls = 0
                self.strikeouts = 0
                self.hits_allowed = 0
                self.singles_allowed = 0
                self.doubles_allowed = 0
                self.triples_allowed = 0
                self.home_runs_allowed = 0
                self.runs_allowed = 0
                self.at_bats_against = 0
                self.sac_flys_allowed = 0
                self.sac_bunts_allowed = 0
                self.batters_faced = 0
                self.earned_runs = 0
                self.pitch_count = 0
                self.outs_pitched = 0
                self.star_pitches = 0
                self.raw_stars_used = 0
                self.pickoffs = 0
                self.pickoff_attempts = 0
                self.inherited_runs = 0
            
            @property
            def innings_pitched(self):
                return self.outs_pitched / 3
            
            @property
            def total_bases_allowed(self):
                return self.singles_allowed + (2 * self.doubles_allowed) + (3 * self.triples_allowed) + (4 * self.home_runs_allowed)
            @property
            def batting_average_against(self):
                return self.hits_allowed / self.at_bats_against if self.at_bats_against > 0 else 0.0
            
            @property
            def on_base_percentage_against(self):
                return (self.hits_allowed + self.walks + self.bean_balls) / (self.batters_faced - self.sac_bunts_allowed) if (self.batters_faced - self.sac_bunts_allowed) > 0 else 0.0
            
            @property
            def on_base_slugging_against(self):
                return self.on_base_percentage_against + self.slugging_percentage_against
            
            @property
            def slugging_percentage_against(self):
                return self.total_bases_allowed / self.at_bats_against if self.at_bats_against > 0 else 0.0

            @property
            def era_per_9(self):
                if self.earned_runs > 0 and self.innings_pitched == 0:
                    return float('inf')
                
                return (self.earned_runs * 9) / self.innings_pitched if self.innings_pitched > 0 else 0.0
            
            @property
            def era_per_7(self):
                if self.innings_pitched == 0:
                    return float('inf') if self.earned_runs > 0 else 0.0
                
                return (self.earned_runs * 7) / self.innings_pitched if self.innings_pitched > 0 else 0.0
                
            
            @property
            def whip(self):
                if (self.walks + self.hits_allowed) > 0 and self.innings_pitched == 0:
                    return float('inf')
                
                return (self.walks + self.hits_allowed) / self.innings_pitched if self.innings_pitched > 0 else 0.0

            @property
            def stars_used(self):
                return self.raw_stars_used / 50.0
        class Fielding:
            def __init__(self) -> None:
                self.assists = 0
                self.putouts = 0
                self.throwouts = 0
                self.buddy_jump_attempts = 0
                self.buddy_jump_outs = 0
                self.double_plays = 0
                self.triple_plays = 0
                self.errors = 0
                self.bobbles = 0
                self.close_plays_won = 0 # No Close Play Stats are currently being tracked.
                self.close_plays_lost = 0  # No Close Play Stats are currently being tracked.
                
            @property
            def fielding_chances(self):
                return self.putouts + self.assists + self.errors + self.bobbles
            
            @property
            def fielding_percentage(self):
                return (self.putouts + self.assists) / self.fielding_chances if self.fielding_chances > 0 else 0.0
        
        class Running:
            def __init__(self) -> None:
                self.stolen_bases = 0
                self.caught_stealing = 0
                self.steal_attempts = 0
                self.close_plays_won = 0  # No Close Play Stats are currently being tracked.
                self.close_plays_lost = 0  # No Close Play Stats are currently being tracked.
                self.runs = 0
                
            @property
            def caught_stealing_percentage(self):
                return self.caught_stealing / self.steal_attempts if self.steal_attempts > 0 else 0.0
        
class _NoPlayer(Player):
    def __init__(self):
        self.name = "No Player"
        self.team_number = -1
        self.batting_index = -1
        self.def_position = None
        self.baserunner_info = None
        self.stats = Player.Stats()        
NO_PLAYER = _NoPlayer()
    
class PlayerType(IntEnum):
    HUMAN_P1 = 0x00
    HUMAN_P2 = 0x01
    HUMAN_P3 = 0x02
    HUMAN_P4 = 0x03
    CPU = 0xFF
    
    @classmethod
    def from_value(cls, value):
        return cls(value) if value in cls._value2member_map_ else cls.CPU
    
class DefensePosition(Refreshable, Writable):
    def __init__(self, index_address, fielder_pointer_address, name, abbrev):
        self.index = Field(index_address, "u8")
        self.position_name: str = name
        self.fielder_pointer_address = fielder_pointer_address
        self.bobble_flag = Field.from_pointer(fielder_pointer_address, 0x02B2, "u8")
        self.buddy_jump_flag = Field.from_pointer(fielder_pointer_address, 0x0223, "u8")
        self.abbrev:str = abbrev
        self.player: Player | _NoPlayer = NO_PLAYER
    
    def refresh_all(self):
        self.index.refresh()
        
class Runner(Refreshable, Writable):
    def __init__(self, index_address: int, steal_address: int, bases_ran_address: int, base_num: int, base: str):
        self.index = Field(index_address, "s8")
        self.is_stealing = Field(steal_address, "s8")
        self.base_num = base_num
        self.base = base
        self.player: Player | _NoPlayer = NO_PLAYER
        self.bases_ran = Field(bases_ran_address, "u8")
        
    def refresh_all(self):
        self.index.refresh()
        self.is_stealing.refresh()
        
        

def _load_team_branding():
    """
    Load team branding from a JSON or TXT file located next to this module.

    JSON expected formats:
    - { "0": ["Long Name", "Short"], "1": {"long": "Long Name", "short": "Short"}, ... }
      keys may be decimal or hex (e.g. "0x00").

    TXT expected simple format (one entry per line):
      key<sep>Long Name|Short Name
    where key is decimal or 0x hex, sep can be ':' or ',' or TAB or whitespace. Lines starting with '#' are ignored.

    If the file is not present or parsing fails, fall back to a built-in default mapping.
    """
    module_dir = os.path.dirname(__file__)
    resource_dir = module_dir
    if getattr(sys, "frozen", False):
        external_dir = os.path.join(os.path.dirname(sys.executable), "MemoryHandling")
        bundle_dir = os.path.join(getattr(sys, "_MEIPASS", module_dir), "MemoryHandling")
        resource_dir = external_dir if os.path.isdir(external_dir) else bundle_dir

    json_path = os.path.join(resource_dir, "team_branding.json")
    txt_path = os.path.join(resource_dir, "team_branding.txt")
    branding = {}
    branding_short = {}
    try:
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                try:
                    key = int(str(k), 0)
                except Exception:
                    # skip invalid keys
                    continue
                if isinstance(v, (list, tuple)) and len(v) >= 2:
                    long_name, short = v[0], v[1]
                elif isinstance(v, dict):
                    long_name = v.get("long") or v.get("name")
                    short = v.get("short") or v.get("short_name")
                else:
                    # unsupported value, skip
                    continue
                if long_name is None:
                    continue
                branding[key] = long_name
                branding_short[key] = short if short is not None else long_name
        elif os.path.exists(txt_path):
            with open(txt_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # try separators
                    for sep in (":", ",", "\t"):
                        if sep in line:
                            parts = line.split(sep, 1)
                            break
                    else:
                        parts = line.split(None, 1)
                    if len(parts) != 2:
                        continue
                    key_str, names = parts[0].strip(), parts[1].strip()
                    try:
                        key = int(key_str, 0)
                    except Exception:
                        continue
                    if "|" in names:
                        long_name, short = [p.strip() for p in names.split("|", 1)]
                    elif "," in names:
                        long_name, short = [p.strip() for p in names.split(",", 1)]
                    else:
                        long_name, short = names, names
                    branding[key] = long_name
                    branding_short[key] = short
        else:
            # no external file found -> trigger fallback
            raise FileNotFoundError
    except Exception:
        # fallback to built-in defaults
        branding = {
            0x00: "Mario Fireballs",
            0x01: "Luigi Knights",
            0x02: "DK Wilds",
            0x03: "Diddy Monkeys",
            0x04: "Peach Monarchs",
            0x05: "Daisy Flowers",
            0x06: "Yoshi Eggs",
            0x07: "Bowser Monsters",
            0x08: "Wario Muscles",
            0x09: "Waluigi Spitballs",
            0x0A: "Birdo Bows",
            0x0B: "Jr. Rookies"
        }
        branding_short = {
            0x00: "Fireballs",
            0x01: "Knights",
            0x02: "Wilds",
            0x03: "Monkeys",
            0x04: "Monarchs",
            0x05: "Flowers",
            0x06: "Eggs",
            0x07: "Monsters",
            0x08: "Muscles",
            0x09: "Spitballs",
            0x0A: "Bows",
            0x0B: "Rookies"
        }
    return branding, branding_short


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
        pitching_index_address,
    ):
        
        self.players: list[Player] = []

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
        self.meter: Field
        self.player_type = Field(player_type_address, "u8", lookup=self.PLAYER_TYPE)
        self.batting_or_fielding = Field(batting_fielding_address, "u8", lookup=self.OFFENSE_DEFENSE)
        self.score_by_inning = [0] * 9
        self.pitching_index = Field(pitching_index_address, "u8")
        self.position_indexes = self.PositionIndexes()
        self.batting_index  = 0  # Current batter index in lineup (0-8)
        self.hits = Field(0xFF, "u8")  # Placeholder, will be set to actual address in Game.__init__
        self.starting_lineup_set = False
        self.starting_lineup: dict[Player, str] = {}
        self.pitcher_order: list[Player] = []

       
        
    @property
    def name(self):
        return self.branding.display
    
    @property
    def short_name(self):
        return self.TEAM_BRANDING_SHORT.get(self.branding.value)
        
            
        
        
    def get_player_index(self,player_ref):
        if isinstance(player_ref,str):
            player_ref = getattr(self, player_ref, None)
            if player_ref is None:
                raise ValueError(f"No player named '{player_ref}' on this team")
        else:
            return player_ref.batting_index
    
    
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
    
    PLAYER_TYPE = {
        0xFF: "CPU",
        0x00: "Player",
        0x01: "Player",
        0x02: "Player",
        0x03: "Player",
    }
    TEAM_BRANDING, TEAM_BRANDING_SHORT = _load_team_branding()
    
    
    OFFENSE_DEFENSE = {
        0x00: "BATTING",
        0x01: "FIELDING"
    }
        
class _NoTeam(Team):
    def __init__(self):
        self.players = []
        self.branding = Field.from_constant(0xFF, "u8", lookup=self.TEAM_BRANDING)
        self.player_type = Field.from_constant(0xFF, "u8", lookup=PlayerType)
        self.batting_or_fielding = Field.from_constant(0xFF, "u8", lookup=self.OFFENSE_DEFENSE)
        self.position_indexes = self.PositionIndexes()
        self.hits = Field.from_constant(0xFF, "u8")

NO_TEAM = _NoTeam()
        
class Game(Refreshable, Writable):
    def __init__(self, team1: Team, team2: Team):
        self.team1: Team = team1
        self.team2: Team = team2
        self.regulation_innings = Field(0x80794328, "u8")
        self.current_inning = Field(0x900D5D97, "u8")
        self.outs = Field(0x900D5AA9, "u8")
        self.pitches = Field(0x900D692C, "u8")
        self.balls = Field(0x900D5AA8,"u8")
        self.strikes = Field(0x900D5AA7, "u8")
        self.current_pitcher = self.CurrentPitcher()
        self.current_batter = self.CurrentBatter()
        self.game_state = Field(0x900d5c28, "u8", lookup=self.STATE)
        self.last_state = Field(0x900d5c29, "u8", lookup=self.STATE)
        self.being_played = True
        self.def_positions = self.DefensePositions()
        self.ball_possession = self.BallPossession()
        self.baserunners = self.Baserunners()
        self.this_pitch = self.ThisPitch()
        self.runs_this_inning = 0
        self.runs_this_pitch = 0
        self.match_started = False
        self.match_quit_early = False
        self.game_timer = Field(0x900dfcfc, "u32")
        self.stadium = Field(0x811F769D, "u8",lookup=self.MAP)
        self.time_of_day = Field(0x811F769F, "u8",lookup=self.TIME_OF_DAY)
        self.rolling_slowdown = Field(0x806255A0 + (20 * self.stadium.value), "f32")
        self.batters_this_inning = Field(0x900D5E35, "u8")
        self.ball_was_hit = Field(0x900d6a94, "u8")
        self.inning_half = Field(0x900D5E25, "u8")
        self.in_replay = Field.from_pointer(0x80794C5C, 0x0013561B, "u8")
        
        self.left_field_airborne_flag = Field(0x900DAED2, "u8")
        self.center_field_airborne_flag = Field(0x900DB1BE, "u8")
        self.right_field_airborne_flag = Field(0x900DB4AA, "u8")

        self.left_field_buddy_jump_flag = Field.from_pointer(0x80708D90, 0x0223, "u8")
        self.center_field_buddy_jump_flag = Field.from_pointer(0x80708D94, 0x0223, "u8")
        self.right_field_buddy_jump_flag = Field.from_pointer(0x80708D98, 0x0223, "u8")
        
        self.star_costs = self.StarCosts()
        self.mercy_flag = Field(0x80794329, "u8")
        self.stars_flag = Field(0x8079432A, "u8")
        self.item_flag = Field(0x8079432B, "u8")
        self.home_run_flag = Field(0x900D953C, "u8")
        self.home_team: Team
        self.away_team: Team
        self.position_nums = {
            0: self.def_positions.pitcher,
            1: self.def_positions.catcher,
            2: self.def_positions.first_base,
            3: self.def_positions.second_base,
            4: self.def_positions.third_base,
            5: self.def_positions.shortstop,
            6: self.def_positions.left_field,
            7: self.def_positions.center_field,
            8: self.def_positions.right_field,
        }
        self.stat_tracker_started_during_match: bool = False


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
        self.current_batter.index.refresh()
        while True:
            Index = self.current_batter.index.value
            if Index >= 0 and Index <= 8:
                break
            else:
                self.current_batter.index.refresh()
        return self.offense_team.players[Index]

    def get_current_batter_index(self)-> int:
        self.current_batter.index.refresh()
        while True:
            Index = self.current_batter.index.value
            if Index >= 0 and Index <= 8:
                break
            else:
                self.current_batter.index.refresh()
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
    
    
    
    def set_def_positions(self):
        def_players = self.defense_team.players
        off_players = self.offense_team.players
        pos = self.def_positions
        
        pos.refresh_all()
        for i in range(9):
            off_players[i].def_position = None
        
        positions_list = [
            pos.pitcher, pos.catcher, pos.first_base, pos.second_base,
            pos.third_base, pos.shortstop, pos.left_field, pos.center_field,
            pos.right_field
        ]
        for position in positions_list:
            position.player = NO_PLAYER
            
        for position in positions_list:
            player: Player = def_players[position.index.value]
            player.def_position = position
            position.player = player


    class StarCosts(Refreshable, Writable):
        def __init__(self) -> None:
            self.regular_star_cost = Field(0x8062bd48, "s16")
            self.captain_star_cost = Field(0x8062bd4A, "s16")
            self.non_main_captain_star_cost = Field(0x8062bd42, "s16")
            
    class DefensePositions(Refreshable, Writable):
        def __init__(self):
            self.pitcher = DefensePosition(0x900d9b45, 0x80708D78, "Pitcher", "P")
            self.catcher = DefensePosition(0x900d9e31, 0x80708D7C,  "Catcher", "C")
            self.first_base = DefensePosition(0x900da11d, 0x80708D80, "First Base", "1B")
            self.second_base = DefensePosition(0x900da409, 0x80708D84, "Second Base", "2B")
            self.third_base = DefensePosition(0x900da6f5, 0x80708D88, "Third Base", "3B")
            self.shortstop = DefensePosition(0x900da9e1, 0x80708D8C, "Shortstop", "SS")
            self.left_field = DefensePosition(0x900daccd, 0x80708D90, "Left Field", "LF")
            self.center_field = DefensePosition(0x900dafb9, 0x80708D94, "Center Field", "CF")
            self.right_field = DefensePosition(0x900db2a5, 0x80708D98, "Right Field", "RF")
        
        def refresh_all(self):
            positions = [
                self.pitcher, self.catcher, self.first_base, self.second_base,
                self.third_base, self.shortstop, self.left_field, self.center_field,
                self.right_field
            ]

            for position in positions:
                position.index.refresh()
            
        
        def get_all_position_players(self) -> dict[str, Player]:
            self.refresh_all()
            return {
                "P": self.pitcher.player,
                "C": self.catcher.player,
                "1B": self.first_base.player,
                "2B": self.second_base.player,
                "3B": self.third_base.player,
                "SS": self.shortstop.player,
                "LF": self.left_field.player,
                "CF": self.center_field.player,
                "RF": self.right_field.player,
            }
            
        def get_all_players_at_positions(self) -> dict[Player, str]:
            self.refresh_all()
            return {
                self.pitcher.player: "P",
                self.catcher.player: "C",
                self.first_base.player: "1B",
                self.second_base.player: "2B",
                self.third_base.player: "3B",
                self.shortstop.player: "SS",
                self.left_field.player: "LF",
                self.center_field.player: "CF",
                self.right_field.player: "RF"
            }

        def get_all_position_players_list(self) -> list[Player]:
            self.refresh_all()
            return [
                self.pitcher.player,
                self.catcher.player,
                self.first_base.player,
                self.second_base.player,
                self.third_base.player,
                self.shortstop.player,
                self.left_field.player,
                self.center_field.player,
                self.right_field.player,
            ]

        
        
        def get_position_index(self, position_name: str) -> int:
            self.refresh_all()
            position_map = {
                "P": self.pitcher,
                "C": self.catcher,
                "1B": self.first_base,
                "2B": self.second_base,
                "3B": self.third_base,
                "SS": self.shortstop,
                "LF": self.left_field,
                "CF": self.center_field,
                "RF": self.right_field,
            }
            position = position_map.get(position_name.upper())
            if position is None:
                raise ValueError(f"Invalid position name: {position_name}")
            return position.index.value
        
        
        def get_all_position_indexes(self) -> dict:
            self.refresh_all()
            return {
                "P": self.pitcher.index.value,
                "C": self.catcher.index.value,
                "1B": self.first_base.index.value,
                "2B": self.second_base.index.value,
                "3B": self.third_base.index.value,
                "SS": self.shortstop.index.value,
                "LF": self.left_field.index.value,
                "CF": self.center_field.index.value,
                "RF": self.right_field.index.value,
            }
        


    class Baserunners(Refreshable, Writable):
        def __init__(self):
            self.first_base = Runner(0x900db7cd, 0x900db93c, 0x900DB921, 1, "First Base")
            self.second_base = Runner(0x900db9a1, 0x900dbb0f, 0x900DBAF5, 2, "Second Base")
            self.third_base = Runner(0x900dbb75, 0x900dbce3, 0x900DBCC9, 3, "Third Base")
        
        def refresh_all(self):
            runners = [
                self.first_base, self.second_base, self.third_base
            ]

            for runner in runners:
               runner.index.refresh()
               runner.is_stealing.refresh()
               runner.bases_ran.refresh()
               
    def get_player_on_base(self, base: int):
        if base < 0 or base > 3:
            raise ValueError(f"Invalid base value: {base}")

        if base == 1:
            target = self.baserunners.first_base
        elif base == 2:
            target = self.baserunners.second_base
        else:
            target = self.baserunners.third_base
        
        if target.player is not None:
            return target.player
        elif target.index.value >= 0:
            return self.offense_team.players[target.index.value]
        
        return None

    def set_baserunners(self):
        def_players = self.defense_team.players
        off_players = self.offense_team.players
        baserun = self.baserunners
        baserun.refresh_all()
        
        br_list = [baserun.first_base, baserun.second_base, baserun.third_base]
        
        for player in def_players:
            player.baserunner_info = None
        
        for br in br_list:
            if br.index.value >= 0 and br.index.value <= 8:
                br.player = off_players[br.index.value]
                off_players[br.index.value].baserunner_info = br
            elif br.index.value < 0:
                if br.player is not None:
                    br.player.baserunner_info = None
                    br.player = NO_PLAYER
            else:
                raise ValueError(f"Baserunner index outside of range: {br.index.value}")
 
    class BallPossession(Refreshable,Writable):
        def __init__(self) -> None:
            self.last_to_have_ball_index = Field(0x900d5056, "u8")
            self.current_ball_holder_index = Field(0x900d66c9, "s8")
            self.ball_status = Field(0x900d953a, "u8")
    
    def get_current_ball_holder(self) -> Player | _NoPlayer:
        self.ball_possession.refresh_all()
        holder_index = self.ball_possession.current_ball_holder_index.value
        if holder_index >=0 and holder_index <=8:
            pos = self.position_nums.get(holder_index)
            if pos is not None and pos.player is not NO_PLAYER:
                return pos.player
        return NO_PLAYER
    
    def get_last_player_to_touch_ball(self):
        self.ball_possession.refresh_all()
        last_holder_index = self.ball_possession.last_to_have_ball_index.value
        if last_holder_index >=0 and last_holder_index <=8:
            pos = self.position_nums.get(last_holder_index)
            if pos is not None:
                return pos.player
        return NO_PLAYER
    
    class ThisPitch(Refreshable, Writable):
        def __init__(self):
            self.runs = Field(0x900D5E30, "u8")
            self.outs = Field(0x900D5E31, "u8")
            self.bean_ball_flag = Field(0x900d6a95, "s8")
            self.out_runner_1 = Field(0x900d5aa0, "s16")
            self.out_runner_2 = Field(0x900d5aa2, "s16")
            self.out_runner_3 = Field(0x900d5aa4, "s16")
            self.fair_or_foul = Field(0x900D9516, "s16")
            self.num_bases_ran = Field(0x900D66D2, "s8")
            
        
        def get_current_out_runner(self):
            self.outs.refresh()
            
            if self.outs.value == 0:
                return self.out_runner_1
            elif self.outs.value == 1:
                return self.out_runner_2
            elif self.outs.value == 2:
                return self.out_runner_3
            else:
                return None
        
        def get_out_runner_by_num(self, val: int):
            if val < 1 or val > 3:
                raise ValueError(f"Invalid Input {val}. Value must be 1, 2 or 3")
            if val == 1:
                return self.out_runner_1
            elif val == 2:
                return self.out_runner_2
            else:
                return self.out_runner_3
                
                    
        
    class CurrentPitcher(Refreshable, Writable):
        def __init__(self):
            self.index = Field(0x900d9b45, "u8")
            self.id = Field(0x900d9b47, "u8", lookup = CHAR_ID_TO_NAME)
            self.captain_star_pitch = Field(0x900D6929, "u8")
        
        def get_current_pitcher_name(self):
            return self.id.display


    class CurrentBatter(Refreshable, Writable):
        def __init__(self):
            self.id = Field(0x900d69ef, "u8", lookup = CHAR_ID_TO_NAME)
            self.index = Field(0x900DB5F9, "u8")
            self.bases_ran = Field(0x900DB74D, "u8")
            
        def get_current_batter_name(self):
            return self.id.display
                    
    
    
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
    0x1D: "CHANGE_LINEUP",
    0x1E: "CONTROLLER_TYPE_SCREEN",
    0x1F: "CHALLENGE_MODE_ITEMS",
    0x20: "REMATCH",
}
    
    
    
        
        
        
        
                                   
        
        
        
        
            
    
    
    
        
        
                          
            
    
        
