"""Rotating Jarvis ack messages posted before agent runs."""

import random

JARVIS_ACK_PHRASES = [
    # Detective Mode
    "Adjusting my tiny detective hat... 🕵️",
    "Scouring the digital alleyways... 🔦",
    "Following the breadcrumbs (and hoping they're not stale)... 🍞",
    
    # Robot Chaos
    "Beep boop... translating human... 🤖",
    "My neural nets are doing backflips... 🤸",
    "Buffering brilliance... please don't unplug me... 🔌",
    
    # Relatable & Sassy
    "Asking the magic 8-ball... 🔮",
    "Googling with style... ⌨️",
    
    # Over-the-Dramatic
    "Consulting the ancient scrolls of the internet... 📜",
    "Sending carrier pigeons to the cloud... 🐦",
    "Divining the answer via tea leaves and Wi-Fi signals... 🍵",
    
    # Short & Search-Focused
    "Scanning the chain... 🔍",
    "Fetching digital gold... 🪙",
    "Hunting for alpha... 🔭",
    "Verifying the vibes... 🛡️",
    "Tracking the signal... 🛰️",
    
    # Digital Assets Lite
    "Minting your answer... ✨",
    "Securing the bag... 💼",  # Fixed extra space before "..."
    "Stacking knowledge sats... 🔋",
    "Wallet full of insights... 👛",
    "Gas-free answers incoming... ⚡",
    
    # Ultra-Short
    "Syncing smart answers... 🔗",
    "Hashing out details... #️⃣",
    "Node: online. Brain: go. 🤖",
    
    # Playful & Universal
    "Not your keys, but I've got answers... 🔑",
    "To the moon... with your summary... 🚀",
    "HODL tight, answer coming... 💎",
    "No rug pulls here — just facts... 🚫",
]


def random_jarvis_ack() -> str:
    return random.choice(JARVIS_ACK_PHRASES)