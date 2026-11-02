# NBA Late-Game Intelligence Engine
# 
# This package contains the NBA late-game scoring engine that processes
# real NBA play-by-play data to provide comprehensive late-game intelligence
# for sports betting and prediction systems.

__version__ = "1.0.0"

from .nba_ingestor import (
    get_db,
    fetch_nba_summary,
    fetch_nba_play_by_play_fallback,
    parse_name_from_play_text,
    parse_nba_play_from_detailed,
    ingest_nba_game,
)

__all__ = [
    'get_db',
    'fetch_nba_summary',
    'fetch_nba_play_by_play_fallback',
    'parse_name_from_play_text',
    'parse_nba_play_from_detailed',
    'ingest_nba_game',
]
