# Probe targets for active connectivity verification.
#
# Each target can be:
#   url  – HTTP/HTTPS probe (normal web request)
#   ip   – TCP connect probe on the given port(s)
#           Port 80/443: timeout = possibly down, RST = host UP (not an outage)
#           Trying multiple ports: all-timeout -> stronger outage signal
#
# This data is only used when a country ALREADY has an IODA/OONI event.
# Probes confirm or deny existing events — they never create new ones.
#
# IP sources: RIPE NCC, ARIN, APNIC delegated stats; verified against BGP tables.

GLOBAL_ANCHORS = [
    {"url": "https://1.1.1.1/cdn-cgi/trace", "desc": "Cloudflare"},
    {"url": "https://dns.google",              "desc": "Google DNS"},
    {"url": "https://9.9.9.9",                 "desc": "Quad9"},
]

COUNTRY_TARGETS = {

    "IR": {"name": "Iran", "targets": [
        {"url": "https://www.irna.ir",           "desc": "IRNA news"},
        {"url": "https://www.isna.ir",           "desc": "ISNA news"},
        {"ip": "5.200.128.1",   "ports": [80,443], "desc": "TCI IP block"},
        {"ip": "91.108.56.1",   "ports": [80,443], "desc": "Irancell IP block"},
    ]},

    "CN": {"name": "China", "targets": [
        {"url": "https://www.baidu.com",         "desc": "Baidu"},
        {"url": "https://www.xinhuanet.com",      "desc": "Xinhua"},
        {"ip": "202.108.22.5",  "ports": [80,443], "desc": "Baidu IP"},
        {"ip": "101.227.128.1", "ports": [80],     "desc": "China Telecom"},
    ]},

    "RU": {"name": "Russia", "targets": [
        {"url": "https://www.kremlin.ru",        "desc": "Kremlin"},
        {"url": "https://vk.com",                "desc": "VKontakte"},
        {"ip": "212.188.1.6",   "ports": [53,80],  "desc": "Rostelecom DNS"},
    ]},

    "MM": {"name": "Myanmar", "targets": [
        {"url": "http://www.mpt.net.mm",         "desc": "MPT"},
        {"ip": "203.81.64.1",   "ports": [80,443], "desc": "MPT IP block"},
    ]},

    "BY": {"name": "Belarus", "targets": [
        {"url": "https://president.gov.by",      "desc": "Belarus presidency"},
        {"ip": "217.21.40.1",   "ports": [80,443], "desc": "Beltelecom"},
    ]},

    "KZ": {"name": "Kazakhstan", "targets": [
        {"url": "https://www.akorda.kz",         "desc": "Kazakhstan presidency"},
        {"ip": "195.47.240.1",  "ports": [80,443], "desc": "Kazakhtelecom"},
    ]},

    "CU": {"name": "Cuba", "targets": [
        {"url": "https://www.granma.cu",         "desc": "Granma"},
        {"ip": "152.206.0.1",   "ports": [80,443], "desc": "ETECSA IP block"},
    ]},

    "VE": {"name": "Venezuela", "targets": [
        {"url": "https://www.vtv.gob.ve",        "desc": "VTV state TV"},
        {"ip": "190.24.0.1",    "ports": [80,443], "desc": "CANTV"},
    ]},

    "ET": {"name": "Ethiopia", "targets": [
        {"url": "https://www.fbc.gov.et",        "desc": "FBC broadcaster"},
        {"ip": "196.188.120.1", "ports": [80,443], "desc": "Ethio Telecom"},
    ]},

    "NG": {"name": "Nigeria", "targets": [
        {"url": "https://www.mtn.com.ng",        "desc": "MTN Nigeria"},
        {"ip": "196.216.2.1",   "ports": [80,443], "desc": "MTN IP block"},
        {"ip": "41.203.64.1",   "ports": [80,443], "desc": "Airtel Nigeria"},
    ]},

    "SD": {"name": "Sudan", "targets": [
        {"url": "https://www.sudatel.sd",        "desc": "Sudatel ISP"},
        {"ip": "41.223.140.1",  "ports": [80,443], "desc": "Sudan Telecom"},
    ]},

    "TR": {"name": "Turkey", "targets": [
        {"url": "https://www.hurriyet.com.tr",   "desc": "Hurriyet"},
        {"ip": "78.189.0.1",    "ports": [80,443], "desc": "Turk Telekom"},
    ]},

    "PK": {"name": "Pakistan", "targets": [
        {"url": "https://www.ptcl.com.pk",       "desc": "PTCL"},
        {"ip": "202.142.160.1", "ports": [80,443], "desc": "PTCL IP block"},
    ]},

    "IN": {"name": "India", "targets": [
        {"url": "https://www.airtel.in",         "desc": "Airtel"},
        {"ip": "121.242.0.1",   "ports": [80,443], "desc": "BSNL IP block"},
    ]},

    "UA": {"name": "Ukraine", "targets": [
        {"url": "https://www.president.gov.ua",  "desc": "Ukraine presidency"},
        {"ip": "178.150.0.1",   "ports": [80,443], "desc": "Kyivstar"},
    ]},

    "SY": {"name": "Syria", "targets": [
        {"url": "https://www.sana.sy",           "desc": "SANA"},
        {"ip": "94.186.0.1",    "ports": [80,443], "desc": "STE Syria"},
    ]},

    "LY": {"name": "Libya", "targets": [
        {"ip": "41.208.64.1",   "ports": [80,443], "desc": "LTT IP block"},
        {"ip": "156.160.0.1",   "ports": [80,443], "desc": "Libyana"},
    ]},

    "IQ": {"name": "Iraq", "targets": [
        {"url": "https://www.uruklink.net",      "desc": "Uruklink ISP"},
        {"ip": "89.32.0.1",     "ports": [80,443], "desc": "ITC Iraq"},
    ]},

    "YE": {"name": "Yemen", "targets": [
        {"ip": "134.35.0.1",    "ports": [80,443], "desc": "TeleYemen IP block"},
    ]},

    "KM": {"name": "Comoros", "targets": [
        {"ip": "196.200.96.1",  "ports": [80,443], "desc": "Comoros Telecom (AS6713)"},
        {"ip": "196.200.97.1",  "ports": [80],     "desc": "Comoros Telecom alt"},
    ]},

    "GN": {"name": "Guinea", "targets": [
        {"ip": "196.224.144.1", "ports": [80,443], "desc": "Sotelgui"},
        {"ip": "41.222.192.1",  "ports": [80,443], "desc": "Orange Guinea"},
    ]},

    "AL": {"name": "Albania", "targets": [
        {"url": "https://www.telekom.al",        "desc": "Telekom Albania"},
        {"ip": "31.22.0.1",     "ports": [80,443], "desc": "ALBtelecom"},
    ]},

    "TM": {"name": "Turkmenistan", "targets": [
        {"ip": "195.144.220.1", "ports": [80,443], "desc": "Turkmentelecom"},
    ]},

    "GP": {"name": "Guadeloupe", "targets": [
        {"ip": "217.108.64.1",  "ports": [80,443], "desc": "Orange Guadeloupe"},
    ]},

    "PS": {"name": "Palestine", "targets": [
        {"url": "https://wafa.ps",               "desc": "WAFA news agency"},
        {"url": "https://www.paltel.ps",         "desc": "PalTel"},
        {"url": "https://www.ooredoo.ps",        "desc": "Ooredoo Palestine"},
    ]},

    "AF": {"name": "Afghanistan", "targets": [
        {"url": "https://www.roshan.af",         "desc": "Roshan"},
        {"url": "https://www.afghanwireless.com","desc": "Afghan Wireless"},
        {"url": "https://8am.media",             "desc": "8am Media (news)"},
    ]},
}
