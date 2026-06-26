#!/usr/bin/env python3
"""
Themarr - Plex Theme Downloader
Downloads theme music from Plex for TV shows and saves them as theme.mp3 files
in the corresponding TV show folders.
"""

import os
import sys
import logging
from pathlib import Path
from typing import Optional, Dict, List
from plexapi.server import PlexServer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PlexThemeDownloader:
    """Downloads theme music from Plex server using plexapi."""
    
    def __init__(self, plex_url: str, plex_token: str):
        """Initialize Plex client."""
        try:
            self.plex = PlexServer(plex_url.rstrip('/'), plex_token)
            logger.info(f"Connected to Plex server at {plex_url}")
        except Exception as e:
            logger.error(f"Failed to connect to Plex: {e}")
            raise
    
    def get_tv_library(self):
        """Get the TV Shows library."""
        try:
            for section in self.plex.library.sections():
                if section.type == 'show':
                    logger.info(f"Found TV library: {section.title}")
                    return section
            logger.error("No TV Shows library found")
            return None
        except Exception as e:
            logger.error(f"Failed to get TV library: {e}")
            return None
    
    def get_show_path(self, show) -> Optional[str]:
        """Get the show folder path directly from Plex metadata.
        
        Show objects in plexapi have a .locations property that gives the exact
        filesystem paths where the show is stored.
        
        Args:
            show: PlexAPI Show object
            
        Returns:
            Full path to show folder, or None if not available
        """
        try:
            # Use plexapi's locations property - this gives us direct filesystem paths
            if hasattr(show, 'locations') and show.locations:
                # locations is a list; return the first one
                show_path = show.locations[0]
                logger.debug(f"Got path for {show.title}: {show_path}")
                return show_path
            else:
                logger.debug(f"No locations available for {show.title}")
                return None
            
        except Exception as e:
            logger.debug(f"Could not get path for {show.title}: {e}")
            return None
    
    def download_theme(self, show, output_path: Path) -> bool:
        """Download theme file from Plex.
        
        Args:
            show: PlexAPI Show object
            output_path: Path to save the theme.mp3 file
            
        Returns:
            True if download successful, False otherwise
        """
        try:
            if not show.theme:
                logger.debug(f"No theme for {show.title}")
                return False
            
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Download the theme using the Plex server
            theme_url = self.plex.url(show.theme, includeToken=True)
            response = self.plex._session.get(
                theme_url,
                stream=True,
                timeout=30
            )
            response.raise_for_status()
            
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            logger.info(f"Downloaded theme to {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to download theme for {show.title}: {e}")
            return False


class TVShowScanner:
    """Scans local TV show folders."""
    
    def __init__(self, tv_shows_path: str):
        """
        Initialize scanner.
        
        Args:
            tv_shows_path: Root path containing TV show folders
        """
        self.tv_shows_path = Path(tv_shows_path)
        if not self.tv_shows_path.exists():
            raise ValueError(f"TV shows path does not exist: {tv_shows_path}")
    
    def scan_shows(self) -> Dict[str, Dict]:
        """
        Scan TV show folders and check for theme.mp3 files.
        
        Returns:
            Dictionary mapping show folder names to their theme status
        """
        shows = {}
        for show_folder in self.tv_shows_path.iterdir():
            if not show_folder.is_dir():
                continue
            
            theme_path = show_folder / 'theme.mp3'
            shows[show_folder.name] = {
                'path': show_folder,
                'has_local_theme': theme_path.exists(),
                'theme_path': theme_path
            }
        
        return shows


def normalize_show_name(name: str) -> str:
    """Normalize show name for fuzzy matching.
    
    Removes special characters, spaces, and year information in parentheses.
    """
    import re
    # Remove year in parentheses like (2025), (2026)
    normalized = re.sub(r'\s*\(\d{4}\)\s*', '', name)
    # Remove other special characters and normalize spaces
    normalized = normalized.lower().replace(' ', '').replace('-', '').replace('_', '').replace('(', '').replace(')', '')
    return normalized


def match_shows(local_shows: Dict, plex_shows: List, plex_client: PlexThemeDownloader, 
                verbose: bool = False, overwrite: bool = False) -> Dict:
    """Match Plex shows to local folders based on exact filesystem paths from Plex metadata.
    
    Args:
        local_shows: Dict of local folders
        plex_shows: List of Plex shows
        plex_client: PlexThemeDownloader instance
        verbose: Print verbose output
        overwrite: If True, re-download themes even if they already exist locally
    """
    results = {
        'matched': [],
        'already_have_theme': [],
        'no_theme_in_plex': [],
        'no_local_match': []
    }
    
    for plex_show in plex_shows:
        title = plex_show.title
        
        # Check if Plex show has a theme
        if not plex_show.theme:
            results['no_theme_in_plex'].append(title)
            if verbose:
                logger.info(f"⊘ SKIPPED (no theme in Plex): {title}")
            continue
        
        # Get the exact show folder path from Plex (e.g., /tv/Show Name (Year))
        show_path = plex_client.get_show_path(plex_show)
        
        if not show_path:
            results['no_local_match'].append(title)
            logger.info(f"⊘ NO LOCAL MATCH: {title}")
            if verbose:
                logger.debug(f"  Could not get path from Plex")
            continue
        
        # Extract just the folder name from the full path (e.g., "Show Name (Year)" from "/tv/Show Name (Year)")
        show_folder_name = Path(show_path).name
        
        # Try to match to local folder (case-insensitive exact match)
        local_show = None
        for local_name, local_info in local_shows.items():
            if local_name.lower() == show_folder_name.lower():
                local_show = local_name
                break
        
        if not local_show:
            results['no_local_match'].append(title)
            logger.info(f"⊘ NO LOCAL MATCH: {title} (Plex folder: {show_folder_name})")
            continue
        
        # Check if local folder already has theme
        if local_shows[local_show]['has_local_theme']:
            if overwrite:
                # Overwrite mode: add to matched list to re-download
                results['matched'].append({
                    'title': title,
                    'show': plex_show,
                    'local_folder': local_show,
                    'theme_path': local_shows[local_show]['theme_path']
                })
                if verbose:
                    logger.info(f"✓ MATCHED (overwriting existing): {title}")
            else:
                # Normal mode: skip
                results['already_have_theme'].append(title)
                if verbose:
                    logger.info(f"⊘ SKIPPED (already has theme): {title}")
            continue
        
        # Ready to download!
        results['matched'].append({
            'title': title,
            'show': plex_show,
            'local_folder': local_show,
            'theme_path': local_shows[local_show]['theme_path']
        })
        
        if verbose:
            logger.info(f"✓ MATCHED: {title}")
    
    return results


def main():
    """Main entry point."""
    plex_url = os.getenv('PLEX_URL', 'http://plex.local.timo.be:32400')
    plex_token = os.getenv('PLEX_TOKEN')
    tv_path = os.getenv('TV_SHOWS_PATH', '/tv')
    verbose = os.getenv('VERBOSE', 'false').lower() == 'true'
    verbose_matching = os.getenv('VERBOSE_MATCHING', 'false').lower() == 'true'
    overwrite = os.getenv('OVERWRITE', 'false').lower() == 'true'
    
    if not plex_token:
        logger.error("PLEX_TOKEN environment variable not set")
        sys.exit(1)
    
    if not Path(tv_path).exists():
        logger.error(f"TV_SHOWS_PATH does not exist: {tv_path}")
        sys.exit(1)
    
    logger.info(f"Plex URL: {plex_url}")
    logger.info(f"TV Shows Path: {tv_path}")
    if overwrite:
        logger.info("⚠️  OVERWRITE MODE ENABLED - Will replace existing theme.mp3 files")
    
    # Initialize Plex client
    plex_client = PlexThemeDownloader(plex_url, plex_token)
    
    # Get TV library
    tv_library = plex_client.get_tv_library()
    if not tv_library:
        sys.exit(1)
    
    # Get all shows
    all_shows = tv_library.all()
    logger.info(f"Found {len(all_shows)} shows in Plex library")
    
    shows_with_themes = [s for s in all_shows if s.theme]
    logger.info(f"  - With themes: {len(shows_with_themes)}")
    logger.info(f"  - Without themes: {len(all_shows) - len(shows_with_themes)}")
    
    # Scan local folders
    scanner = TVShowScanner(tv_path)
    local_shows = scanner.scan_shows()
    logger.info(f"Found {len(local_shows)} local folders")
    
    # Match shows (with overwrite flag)
    matches = match_shows(local_shows, all_shows, plex_client, verbose=verbose_matching, overwrite=overwrite)
    
    # Print summary
    print("\n" + "="*60)
    print("MATCHING SUMMARY")
    print("="*60)
    print(f"Total Plex shows: {len(all_shows)}")
    print(f"  - With themes: {len(shows_with_themes)}")
    print(f"  - Without themes: {len(all_shows) - len(shows_with_themes)}")
    print(f"Total local folders: {len(local_shows)}")
    print()
    print("Matching Results:")
    print(f"  ✓ Matched (ready to download): {len(matches['matched'])}")
    print(f"  ⊘ Skipped (already have theme): {len(matches['already_have_theme'])}")
    print(f"  ⊘ Skipped (no theme in Plex): {len(matches['no_theme_in_plex'])}")
    print(f"  ⊘ No local match found: {len(matches['no_local_match'])}")
    print()
    
    # Print unmatched
    if matches['no_local_match']:
        print(f"Sample of {len(matches['no_local_match'])} unmatched Plex shows (with themes):")
        for title in matches['no_local_match'][:10]:
            print(f"    - {title}")
        if len(matches['no_local_match']) > 10:
            print(f"    ... and {len(matches['no_local_match']) - 10} more")
    
    # Print local folders status
    print("\nLocal folders (first 20):")
    matched_folders = {m['local_folder'].lower() for m in matches['matched']}
    
    for i, (folder_name, info) in enumerate(sorted(local_shows.items())):
        if i >= 20:
            print(f"    ... and {len(local_shows) - 20} more")
            break
        
        has_match = folder_name.lower() in matched_folders or info['has_local_theme']
        symbol = "✓" if has_match else "✗"
        print(f"    {symbol} {folder_name}")
    
    print("="*60 + "\n")
    
    # Download themes
    if matches['matched']:
        logger.info(f"Matched {len(matches['matched'])} shows for theme download")
        
        downloaded = 0
        for match in matches['matched']:
            if plex_client.download_theme(match['show'], match['theme_path']):
                downloaded += 1
        
        logger.info(f"Successfully downloaded {downloaded}/{len(matches['matched'])} themes")
    else:
        logger.info("No new themes to download")
    
    # Clean up empty (0KB) theme files
    logger.info("Cleaning up empty theme files...")
    empty_themes = []
    for theme_file in Path(tv_path).glob('*/theme.mp3'):
        if theme_file.stat().st_size == 0:
            try:
                theme_file.unlink()
                empty_themes.append(theme_file.parent.name)
                logger.info(f"Removed empty theme: {theme_file.parent.name}")
            except Exception as e:
                logger.error(f"Failed to remove {theme_file}: {e}")
    
    if empty_themes:
        logger.info(f"Removed {len(empty_themes)} empty theme files")
    
    return matches


if __name__ == '__main__':
    main()
