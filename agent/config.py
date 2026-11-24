"""Static configuration: category taxonomy, store registry, tunables."""
from __future__ import annotations

import os
from pathlib import Path

SITE_NAME = "dealgrep"
AGENT_DIR = Path(__file__).resolve().parent
SOURCES_FILE = AGENT_DIR / "sources.json"
FIXTURES_DIR = AGENT_DIR / "tests" / "fixtures"


def env_str(name: str, default: str) -> str:
    """Environment value, treating unset *and* empty as default (GitHub Actions
    passes an empty string for a repository variable that was never created)."""
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else default


def env_int(name: str, default: int) -> int:
    try:
        return int(env_str(name, str(default)))
    except ValueError:
        return default


# Deals not seen by any source for this long are dropped.
STALE_HOURS = env_int("STALE_HOURS", 48)
# Hard cap on the deals file so the page stays fast.
MAX_DEALS = env_int("MAX_DEALS", 1500)
# How many run summaries to keep for the changelog page (168 = one week hourly).
RUN_LOG_LIMIT = env_int("RUN_LOG_LIMIT", 168)
# Price history points kept per deal.
PRICE_HISTORY_LIMIT = 48

# ---------------------------------------------------------------------------
# Categories. `priority` is the matching order (lower wins); list order is the
# display order on the site.  Keywords are matched on word boundaries against
# the lower-cased title + description.
# ---------------------------------------------------------------------------
CATEGORIES: list[dict] = [
    {
        "slug": "electronics",
        "name": "Electronics",
        "blurb": "Headphones, speakers, cameras, wearables and other gadgets.",
        "priority": 90,
        "keywords": [
            "headphones", "earbuds", "airpods", "speaker", "camera",
            "gopro", "smartwatch", "watch", "fitbit", "garmin", "drone", "e-reader",
            "kindle", "charger", "power bank", "usb-c", "cable", "bluetooth",
            "microphone", "webcam", "tracker", "airtag", "electronics",
        ],
    },
    {
        "slug": "computers",
        "name": "Computers & Laptops",
        "blurb": "Laptops, desktops, monitors, components and peripherals.",
        "priority": 20,
        "keywords": [
            "laptop", "notebook", "chromebook", "macbook", "desktop", "pc", "monitor",
            "ssd", "nvme", "hdd", "hard drive", "ram", "ddr5", "ddr4", "gpu",
            "graphics card", "rtx", "radeon", "cpu", "ryzen", "core i7", "core i5",
            "core i9", "motherboard", "keyboard", "mouse", "mechanical", "docking",
            "thunderbolt", "router", "mesh wifi", "nas", "printer", "ipad pro", "1440p", "144hz", "165hz", "ultrawide", "prebuilt",
            "surface", "thinkpad", "psu", "power supply", "case fan", "microsd",
        ],
    },
    {
        "slug": "phones-tablets",
        "name": "Phones & Tablets",
        "blurb": "Smartphones, tablets, cases and carrier deals.",
        "priority": 25,
        "keywords": [
            "iphone", "pixel", "galaxy s", "galaxy z", "galaxy a", "oneplus",
            "smartphone", "phone", "ipad", "tablet", "galaxy tab", "fire tablet",
            "fire hd", "phone case", "screen protector", "unlocked",
        ],
    },
    {
        "slug": "tv-home-theater",
        "name": "TVs & Home Theater",
        "blurb": "Televisions, streaming sticks, receivers and surround sound.",
        "priority": 22,
        "keywords": [
            "tv", "oled", "qled", "4k", "8k", "65-inch", "55-inch", "75-inch",
            "roku", "fire tv", "chromecast", "apple tv", "streaming stick", "soundbar", "projector",
            "home theater", "receiver", "subwoofer", "blu-ray player", "antenna",
        ],
    },
    {
        "slug": "gaming",
        "name": "Gaming",
        "blurb": "Consoles, games, controllers, headsets and PC gaming gear.",
        "priority": 10,
        "keywords": [
            "xbox", "playstation", "ps5", "ps4", "nintendo", "switch", "steam",
            "steam deck", "controller", "gaming", "game pass", "video game", "console", "rpg", "racing wheel",
            "meta quest", "vr", "headset", "joy-con", "dualsense", "gamecube",
            "rog ally", "legion go",
        ],
    },
    {
        "slug": "smart-home",
        "name": "Smart Home",
        "blurb": "Speakers, cameras, doorbells, lights, plugs and robot vacuums.",
        "priority": 30,
        "keywords": [
            "echo", "alexa", "echo dot", "echo show", "nest", "google home", "smart speaker", "smart display",
            "ring doorbell", "doorbell", "security camera", "smart plug",
            "smart bulb", "hue", "smart lock", "thermostat", "robot vacuum",
            "roomba", "eufy", "wyze", "smart home", "homekit",
        ],
    },
    {
        "slug": "home-kitchen",
        "name": "Home & Kitchen",
        "blurb": "Cookware, small appliances, bedding, furniture and decor.",
        "priority": 40,
        "keywords": [
            "air fryer", "instant pot", "pressure cooker", "blender", "coffee maker",
            "espresso", "keurig", "nespresso", "cookware", "knife", "skillet",
            "dutch oven", "stand mixer", "kitchenaid", "vacuum", "dyson", "shark",
            "mattress", "pillow", "sheets", "bedding", "comforter", "sofa", "couch",
            "desk", "chair", "office chair", "rug", "lamp", "cordless vacuum",
            "toaster", "microwave oven", "water bottle", "stanley", "yeti tumbler",
            "storage", "organizer", "humidifier", "air purifier", "heater", "fan", "string lights", "christmas lights", "christmas tree", "decor", "candle",
        ],
    },
    {
        "slug": "appliances",
        "name": "Appliances",
        "blurb": "Refrigerators, washers, dryers, dishwashers and ranges.",
        "priority": 35,
        "keywords": [
            "refrigerator", "fridge", "washer", "dryer", "washing machine",
            "dishwasher", "range", "oven", "cooktop", "freezer", "appliance",
            "appliances", "range hood",
        ],
    },
    {
        "slug": "tools-garden",
        "name": "Tools & Garden",
        "blurb": "Power tools, tool kits, outdoor power equipment and garden gear.",
        "priority": 45,
        "keywords": [
            "drill", "impact driver", "dewalt", "milwaukee", "makita", "ryobi",
            "craftsman", "tool set", "tool kit", "socket set", "saw", "circular saw",
            "miter saw", "pressure washer", "leaf blower", "mower", "lawn mower",
            "chainsaw", "generator", "shop vac", "ladder", "workbench", "tool",
            "tools", "garden", "hose", "grill", "smoker", "fire pit",
        ],
    },
    {
        "slug": "toys-kids",
        "name": "Toys & Kids",
        "blurb": "Toys, games, LEGO, baby gear and kids' essentials.",
        "priority": 50,
        "keywords": [
            "lego", "toy", "toys", "nerf", "barbie", "hot wheels", "playset",
            "plush", "puzzle", "board game", "kids", "toddler", "baby", "stroller",
            "car seat", "diapers", "crib", "pokemon", "action figure", "doll", "ride-on", "train set", "building block", "building blocks",
        ],
    },
    {
        "slug": "fashion",
        "name": "Fashion & Apparel",
        "blurb": "Clothing, shoes, bags, jewelry and accessories.",
        "priority": 55,
        "keywords": [
            "shoes", "sneakers", "boots", "jacket", "coat", "hoodie", "jeans",
            "pants", "shirt", "t-shirt", "dress", "sweater", "fleece", "puffer",
            "nike", "adidas", "levi", "north face", "patagonia", "columbia",
            "backpack", "wallet", "sunglasses", "jewelry", "earrings", "necklace",
            "apparel", "clothing", "socks", "underwear", "bra", "leggings",
            "running shoes", "slippers", "ugg", "crocs",
        ],
    },
    {
        "slug": "beauty-health",
        "name": "Beauty & Health",
        "blurb": "Skincare, grooming, oral care, supplements and wellness.",
        "priority": 60,
        "keywords": [
            "skincare", "moisturizer", "serum", "sunscreen", "shampoo", "razor",
            "shaver", "trimmer", "electric toothbrush", "oral-b", "sonicare",
            "waterpik", "hair dryer", "straightener", "makeup", "perfume",
            "fragrance", "cologne", "vitamin", "supplement", "protein powder",
            "massage gun", "theragun", "blood pressure", "scale", "first aid",
        ],
    },
    {
        "slug": "sports-outdoors",
        "name": "Sports & Outdoors",
        "blurb": "Fitness, camping, cycling, golf and outdoor gear.",
        "priority": 65,
        "keywords": [
            "bike", "bicycle", "e-bike", "ebike", "tent", "sleeping bag", "camping",
            "cooler", "yeti", "hydro flask", "dumbbell", "kettlebell", "treadmill",
            "exercise bike", "rowing", "yoga", "golf", "fishing", "kayak",
            "paddle board", "hiking", "trekking", "ski", "snowboard", "fitness",
            "resistance bands", "pickleball", "basketball", "football", "scooter",
        ],
    },
    {
        "slug": "grocery",
        "name": "Grocery & Household",
        "blurb": "Coffee, snacks, pantry staples and household supplies.",
        "priority": 70,
        "keywords": [
            "coffee", "k-cup", "k-cups", "snack", "snacks", "grocery", "pantry",
            "cereal", "protein bar", "energy drink", "soda", "water", "paper towels",
            "toilet paper", "detergent", "tide", "dish soap", "trash bags",
            "pet food", "dog food", "cat food", "litter", "subscribe & save",
        ],
    },
    {
        "slug": "automotive",
        "name": "Automotive",
        "blurb": "Tires, dash cams, jump starters, car care and accessories.",
        "priority": 75,
        "keywords": [
            "tire", "tires", "dash cam", "jump starter", "car battery", "motor oil",
            "wiper", "car vacuum", "car charger", "floor mats", "tire inflator",
            "obd2", "car wash", "detailing", "automotive", "truck", "trailer",
        ],
    },
    {
        "slug": "books-media",
        "name": "Books & Media",
        "blurb": "Books, e-books, audiobooks, music and movies.",
        "priority": 80,
        "keywords": [
            "book", "books", "ebook", "e-book", "audiobook", "audible", "vinyl",
            "blu-ray", "4k uhd", "dvd", "box set", "comic", "manga", "paperback",
            "hardcover", "magazine",
        ],
    },
    {
        "slug": "software-services",
        "name": "Software & Services",
        "blurb": "Subscriptions, software, VPNs, cloud storage and streaming.",
        "priority": 85,
        "keywords": [
            "subscription", "vpn", "nordvpn", "expressvpn", "software", "antivirus",
            "microsoft 365", "office 365", "adobe", "windows 11", "hulu", "disney+",
            "paramount+", "peacock", "spotify", "youtube premium", "cloud storage",
            "domain", "hosting", "membership", "prime membership", "gift card", "password manager",
        ],
    },
    {
        "slug": "travel",
        "name": "Travel",
        "blurb": "Flights, hotels, cruises, luggage and travel gear.",
        "priority": 87,
        "keywords": [
            "flight", "flights", "airfare", "hotel", "resort", "cruise", "luggage",
            "suitcase", "carry-on", "travel", "airline", "vacation", "rental car",
        ],
    },
    {
        "slug": "other",
        "name": "Everything Else",
        "blurb": "Deals that did not fit a category yet.",
        "priority": 1000,
        "keywords": [],
    },
]

CATEGORY_BY_SLUG = {c["slug"]: c for c in CATEGORIES}
CATEGORY_SLUGS = [c["slug"] for c in CATEGORIES]
RESEARCHABLE_CATEGORIES = [c["slug"] for c in CATEGORIES if c["slug"] != "other"]

# ---------------------------------------------------------------------------
# Stores. `hosts` are matched against the URL hostname (suffix match);
# `aliases` are matched against text such as "[Amazon]" or "at Best Buy".
# ---------------------------------------------------------------------------
STORES: dict[str, dict] = {
    "amazon":     {"name": "Amazon",        "hosts": ["amazon.com", "amzn.to", "amzn.com", "a.co"], "aliases": ["amazon", "amzn"]},
    "bestbuy":    {"name": "Best Buy",      "hosts": ["bestbuy.com", "bby.us"],                      "aliases": ["best buy", "bestbuy", "bby"]},
    "target":     {"name": "Target",        "hosts": ["target.com", "tgt.gifts"],                    "aliases": ["target"]},
    "walmart":    {"name": "Walmart",       "hosts": ["walmart.com"],                                "aliases": ["walmart"]},
    "costco":     {"name": "Costco",        "hosts": ["costco.com"],                                 "aliases": ["costco"]},
    "newegg":     {"name": "Newegg",        "hosts": ["newegg.com"],                                 "aliases": ["newegg"]},
    "bhphoto":    {"name": "B&H Photo",     "hosts": ["bhphotovideo.com"],                           "aliases": ["b&h", "b&h photo", "bhphoto"]},
    "homedepot":  {"name": "Home Depot",    "hosts": ["homedepot.com"],                              "aliases": ["home depot", "homedepot"]},
    "lowes":      {"name": "Lowe's",        "hosts": ["lowes.com"],                                  "aliases": ["lowe's", "lowes"]},
    "ebay":       {"name": "eBay",          "hosts": ["ebay.com"],                                   "aliases": ["ebay"]},
    "apple":      {"name": "Apple",         "hosts": ["apple.com"],                                  "aliases": ["apple store"]},
    "samsung":    {"name": "Samsung",       "hosts": ["samsung.com"],                                "aliases": ["samsung.com"]},
    "dell":       {"name": "Dell",          "hosts": ["dell.com"],                                   "aliases": ["dell.com"]},
    "lenovo":     {"name": "Lenovo",        "hosts": ["lenovo.com"],                                 "aliases": ["lenovo.com"]},
    "hp":         {"name": "HP",            "hosts": ["hp.com"],                                     "aliases": ["hp.com"]},
    "microsoft":  {"name": "Microsoft",     "hosts": ["microsoft.com", "xbox.com"],                  "aliases": ["microsoft store"]},
    "gamestop":   {"name": "GameStop",      "hosts": ["gamestop.com"],                               "aliases": ["gamestop"]},
    "steam":      {"name": "Steam",         "hosts": ["steampowered.com"],                           "aliases": ["steam store"]},
    "woot":       {"name": "Woot",          "hosts": ["woot.com"],                                   "aliases": ["woot"]},
    "kohls":      {"name": "Kohl's",        "hosts": ["kohls.com"],                                  "aliases": ["kohl's", "kohls"]},
    "macys":      {"name": "Macy's",        "hosts": ["macys.com"],                                  "aliases": ["macy's", "macys"]},
    "nike":       {"name": "Nike",          "hosts": ["nike.com"],                                   "aliases": ["nike.com"]},
    "adidas":     {"name": "adidas",        "hosts": ["adidas.com"],                                 "aliases": ["adidas.com"]},
    "rei":        {"name": "REI",           "hosts": ["rei.com"],                                    "aliases": ["rei"]},
    "wayfair":    {"name": "Wayfair",       "hosts": ["wayfair.com"],                                "aliases": ["wayfair"]},
    "sams":       {"name": "Sam's Club",    "hosts": ["samsclub.com"],                               "aliases": ["sam's club", "sams club"]},
    "staples":    {"name": "Staples",       "hosts": ["staples.com"],                                "aliases": ["staples"]},
    "adorama":    {"name": "Adorama",       "hosts": ["adorama.com"],                                "aliases": ["adorama"]},
    "other":      {"name": "Other store",   "hosts": [],                                             "aliases": []},
}

# Aggregator hosts: a link to one of these is not a store link.
AGGREGATOR_HOSTS = ["slickdeals.net", "reddit.com", "redd.it", "dealnews.com", "bensbargains.com", "camelcamelcamel.com"]

# Tracking parameters stripped when canonicalising URLs.
TRACKING_PARAMS = {
    "tag", "ref", "ref_", "linkcode", "linkid", "camp", "creative", "creativeasin",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "cjevent", "irclickid",
    "irgwc", "afsrc", "clickid", "sid", "psc", "th", "smid", "qid", "sr", "keywords",
    "pd_rd_i", "pd_rd_r", "pd_rd_w", "pd_rd_wg", "pf_rd_p", "pf_rd_r", "spm",
    "u1", "subid", "aff", "affid", "affiliate", "ranmid", "ranaid", "ransiteid",
}

# Research agent (Claude) tunables.
RESEARCH_MODEL = env_str("RESEARCH_MODEL", "claude-opus-5-5")
RESEARCH_EFFORT = env_str("RESEARCH_EFFORT", "medium")
RESEARCH_PER_RUN = env_int("RESEARCH_PER_RUN", 2)       # categories researched per hourly run
RESEARCH_HOT = env_str("RESEARCH_HOT", "1").lower() not in ("0", "false", "no", "off")
RESEARCH_MAX_SEARCHES = env_int("RESEARCH_MAX_SEARCHES", 8)   # web searches per request
RESEARCH_STORES = ["Amazon", "Best Buy", "Target", "Walmart", "Costco", "Newegg", "B&H Photo", "Home Depot", "Lowe's"]
