import os
import re
import json
import random
import string
import asyncio
import aiohttp
import argparse
import ssl
import logging
import tempfile
import subprocess
from pathlib import Path
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from urllib.parse import quote, urljoin
import m3u8
import ffmpeg

class AnimePaheDownloader:
    def __init__(self):
        self.host = "https://animepahe.ru"
        self.anime_url = f"{self.host}/anime"
        self.api_url = f"{self.host}/api"
        self.referer_url = self.host
        self.session = None
        self.debug_mode = False
        self.list_only = False
        
        # Create downloads directory
        self.script_path = Path(os.getcwd())
        self.downloads_path = self.script_path / "downloads"
        self.downloads_path.mkdir(exist_ok=True)
        
        # Add better user agent
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    async def init_session(self):
        """Initialize aiohttp session with required headers"""
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        cookie = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
        headers = {
            "User-Agent": self.user_agent,
            "Referer": self.referer_url,
            "Cookie": f"__ddg2_={cookie}",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1"
        }
        
        connector = aiohttp.TCPConnector(ssl=ssl_context, force_close=True)
        self.session = aiohttp.ClientSession(headers=headers, connector=connector)

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    def print_info(self, message: str):
        if not self.list_only:
            print(f"\033[32m[INFO]\033[0m {message}")

    def print_warn(self, message: str):
        if not self.list_only:
            print(f"\033[33m[WARNING]\033[0m {message}")

    def print_error(self, message: str):
        print(f"\033[31m[ERROR]\033[0m {message}")
        raise SystemExit(1)

    async def search_anime(self, query: str) -> List[Dict]:
        try:
            params = {
                "m": "search",
                "q": quote(query)
            }
            
            async with self.session.get(f"{self.host}/api", params=params) as resp:
                if resp.status != 200:
                    self.print_warn(f"Search request failed with status {resp.status}")
                    return []
                    
                data = await resp.json()
                if data["total"] == 0:
                    return []
                    
                return [{
                    "session": item["session"],
                    "title": item["title"],
                    "slug": item["session"]
                } for item in data["data"]]
                
        except Exception as e:
            if self.debug_mode:
                self.print_warn(f"Search error: {str(e)}")
            return []

    async def get_episode_list(self, anime_id: str) -> List[Dict]:
        page = 1
        all_episodes = []
        
        try:
            while True:
                params = {
                    "m": "release",
                    "id": anime_id,
                    "sort": "episode_asc",
                    "page": page
                }
                
                async with self.session.get(self.api_url, params=params) as resp:
                    if resp.status != 200:
                        break
                        
                    data = await resp.json()
                    all_episodes.extend(data["data"])
                    
                    if page >= data["last_page"]:
                        break
                    page += 1
            
            return all_episodes
        except Exception as e:
            if self.debug_mode:
                self.print_warn(f"Episode list error: {str(e)}")
            return []

    async def get_episode_link(self, anime_slug: str, session_id: str, quality: Optional[str] = None) -> str:
        try:
            url = f"{self.host}/play/{anime_slug}/{session_id}"
            headers = {
                "User-Agent": self.user_agent,
                "Referer": self.host,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
            }
            
            async with self.session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    if self.debug_mode:
                        self.print_warn(f"Failed to get page: Status {resp.status}")
                    return None
                    
                html = await resp.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                buttons = []
                for button in soup.find_all('button', attrs={'data-src': True}):
                    resolution = button.get('data-resolution', '')
                    av1 = button.get('data-av1', '1')
                    if av1 == '0':  # Only non-AV1 streams
                        buttons.append((button['data-src'], resolution))
                
                if not buttons:
                    if self.debug_mode:
                        self.print_warn("No download buttons found")
                    return None
                    
                if quality:
                    matching_buttons = [b for b in buttons if b[1] == quality]
                    if matching_buttons:
                        return matching_buttons[0][0]
                
                buttons.sort(key=lambda x: int(x[1]) if x[1].isdigit() else 0)
                return buttons[-1][0]
                
        except Exception as e:
            if self.debug_mode:
                self.print_warn(f"Failed to get episode link: {str(e)}")
            return None

    async def get_playlist_link(self, kwik_url: str) -> str:
        try:
            headers = {
                "User-Agent": self.user_agent,
                "Referer": self.host
            }
            
            async with self.session.get(kwik_url, headers=headers) as resp:
                if resp.status != 200:
                    if self.debug_mode:
                        self.print_warn(f"Failed to get kwik page: Status {resp.status}")
                    return None
                html = await resp.text()

                if self.debug_mode:
                    self.print_info(f"Got kwik page: {len(html)} bytes")

                # Try to find the stream URL directly
                stream_patterns = [
                    r'source\s*=\s*["\']?(https?://[^"\']+\.(?:m3u8|mp4))["\']?',
                    r'file\s*:\s*["\']?(https?://[^"\']+\.(?:m3u8|mp4))["\']?',
                    r'source:["\']?(https?://[^"\']+\.(?:m3u8|mp4))["\']?',
                    r'(https?://[^"\']+\.(?:m3u8|mp4))'
                ]

                for pattern in stream_patterns:
                    match = re.search(pattern, html, re.IGNORECASE)
                    if match:
                        return match.group(1)

                # If direct URL not found, try extracting from obfuscated code
                script_match = re.search(r"eval\(function\(p,a,c,k,e,[rd]\).*?{[^}]+}}\('([^']+)',(\d+),(\d+),'([^']+)'\.split\('\|'\)\)\)", html)
                if script_match:
                    try:
                        with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
                            f.write("""
                            try {
                                function deobfuscate(p,a,c,k,e,d) {
                                    e = function(c) {
                                        return (c < a ? '' : e(parseInt(c/a))) + ((c = c%a) > 35 ? String.fromCharCode(c+29) : c.toString(36))
                                    };
                                    if (!''.replace(/^/, String)) {
                                        while (c--) {
                                            d[e(c)] = k[c] || e(c);
                                        }
                                        k = [function(e) {
                                            return d[e];
                                        }];
                                        e = function() {
                                            return '\\\\w+';
                                        };
                                        c = 1;
                                    }
                                    while (c--) {
                                        if (k[c]) {
                                            p = p.replace(new RegExp('\\\\b' + e(c) + '\\\\b', 'g'), k[c]);
                                        }
                                    }
                                    return p;
                                }

                                const code = '%s';
                                const result = deobfuscate(code, %d, %d, '%s'.split('|'));
                                console.log(result);
                            } catch (error) {
                                console.error(error);
                                process.exit(1);
                            }
                            """ % (
                                script_match.group(1), 
                                int(script_match.group(2)), 
                                int(script_match.group(3)), 
                                script_match.group(4)
                            ))
                            js_file = f.name

                        result = subprocess.check_output(['node', js_file], text=True)
                        
                        # Search for URL in the deobfuscated result
                        for pattern in stream_patterns:
                            match = re.search(pattern, result, re.IGNORECASE)
                            if match:
                                return match.group(1)

                    except Exception as e:
                        if self.debug_mode:
                            self.print_warn(f"Failed to deobfuscate: {str(e)}")
                    finally:
                        try:
                            os.unlink(js_file)
                        except:
                            pass

            if self.debug_mode:
                self.print_warn("No video URL found")
            return None

        except Exception as e:
            if self.debug_mode:
                self.print_warn(f"Failed to get video link: {str(e)}")
            return None

    async def download_episode(self, anime_name: str, episode_id: str, quality: Optional[str] = None):
        try:
            self.print_info(f"Getting link for episode {episode_id}...")
            link = await self.get_episode_link(anime_name, episode_id, quality)
            if not link:
                self.print_warn(f"Failed to get link for episode {episode_id}")
                return

            self.print_info("Getting stream URL...")
            if self.debug_mode:
                self.print_info(f"Processing URL: {link}")
            stream_url = await self.get_playlist_link(link)
            if not stream_url:
                self.print_warn("Failed to get stream URL")
                if self.debug_mode:
                    self.print_info("Try running with -d flag for more debug info")
                return

            if self.list_only:
                print(stream_url)
                return

            output_file = self.downloads_path / f"{anime_name}_EP{episode_id}.mp4"

            if stream_url.endswith('.m3u8'):
                await self.download_m3u8(stream_url, output_file)
            else:  # Direct MP4 link
                await self.download_mp4(stream_url, output_file)
            
            self.print_info(f"Episode {episode_id} downloaded successfully!")
            
        except Exception as e:
            if self.debug_mode:
                import traceback
                traceback.print_exc()
            self.print_warn(f"Failed to download episode: {str(e)}")

    async def download_m3u8(self, playlist_url: str, output_file: Path):
        try:
            async with self.session.get(playlist_url) as resp:
                if resp.status != 200:
                    raise Exception(f"Failed to get playlist: Status {resp.status}")
                playlist = m3u8.loads(await resp.text())

            temp_dir = output_file.parent / f"temp_{output_file.stem}"
            temp_dir.mkdir(exist_ok=True)

            self.print_info("Downloading segments...")
            tasks = []
            for i, segment in enumerate(playlist.segments):
                task = asyncio.create_task(
                    self.download_segment(segment.uri, temp_dir / f"{i:04d}.ts")
                )
                tasks.append(task)

            await asyncio.gather(*tasks)

            self.print_info("Combining segments...")
            with open(temp_dir / "filelist.txt", "w") as f:
                for i in range(len(tasks)):
                    f.write(f"file '{i:04d}.ts'\n")

            try:
                ffmpeg.input(
                    str(temp_dir / "filelist.txt"),
                    f="concat",
                    safe=0
                ).output(
                    str(output_file),
                    c="copy",
                    loglevel="error"
                ).overwrite_output().run(
                    capture_stdout=True,
                    capture_stderr=True
                )
            except ffmpeg.Error as e:
                self.print_error(f"FFmpeg error: {e.stderr.decode()}")

            if not self.debug_mode:
                import shutil
                shutil.rmtree(temp_dir)

        except Exception as e:
            self.print_error(f"Failed to process m3u8: {str(e)}")

    async def download_mp4(self, url: str, output_file: Path):
        try:
            self.print_info("Downloading MP4 file...")
            temp_file = output_file.with_suffix('.temp.mp4')
            
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"Failed to download MP4: Status {resp.status}")
                
                total_size = int(resp.headers.get('content-length', 0))
                chunk_size = 1024 * 1024  # 1MB chunks
                downloaded = 0

                with open(temp_file, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(chunk_size):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size:
                            percent = (downloaded * 100) / total_size
                            print(f"\rDownload Progress: {percent:.1f}%", end="")

                print()  # New line after progress
                temp_file.rename(output_file)

        except Exception as e:
            if temp_file.exists():
                temp_file.unlink()
            raise

    async def download_segment(self, url: str, output_path: Path):
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                async with self.session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        output_path.write_bytes(data)
                        return
                    retry_count += 1
            except Exception as e:
                retry_count += 1
                if retry_count == max_retries:
                    self.print_warn(f"Failed to download segment {url}: {str(e)}")
                    raise
                await asyncio.sleep(1)


async def main():
    parser = argparse.ArgumentParser(description="Download anime from animepahe")
    parser.add_argument("-a", "--anime", help="Anime name to search for")
    parser.add_argument("-s", "--slug", help="Anime slug/UUID")
    parser.add_argument("-e", "--episode", help="Episode number(s) to download")
    parser.add_argument("-r", "--resolution", help="Video resolution (e.g., 1080, 720)")
    parser.add_argument("-l", "--list", action="store_true", help="Show stream URL only")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()

    downloader = AnimePaheDownloader()
    downloader.debug_mode = args.debug
    downloader.list_only = args.list

    try:
        await downloader.init_session()

        if args.anime:
            results = await downloader.search_anime(args.anime)
            if not results:
                downloader.print_error("No results found!")
                return

            print("\nFound anime:")
            for i, result in enumerate(results, 1):
                print(f"{i}. {result['title']}")
            
            choice = int(input("\nSelect anime (number): ")) - 1
            if 0 <= choice < len(results):
                anime = results[choice]
            else:
                downloader.print_error("Invalid selection!")
                return

        elif args.slug:
            anime = {"slug": args.slug}
        else:
            downloader.print_error("Please provide either anime name (-a) or slug (-s)!")
            return

        episodes = await downloader.get_episode_list(anime["slug"])
        if not episodes:
            downloader.print_error("No episodes found!")
            return

        if not args.episode:
            print("\nAvailable episodes:")
            for ep in episodes:
                print(f"Episode {ep['episode']}")
            episode_input = input("\nEnter episode number(s) (e.g., 1-3,5,7-9): ")
        else:
            episode_input = args.episode

        episode_numbers = []
        for part in episode_input.split(","):
            if "-" in part:
                start, end = map(int, part.split("-"))
                episode_numbers.extend(range(start, end + 1))
            else:
                episode_numbers.append(int(part))

        for ep in sorted(set(episode_numbers)):
            for episode in episodes:
                if int(episode['episode']) == ep:
                    await downloader.download_episode(
                        anime["slug"],
                        episode['session'],
                        args.resolution
                    )
                    break

    finally:
        await downloader.close()


if __name__ == "__main__":
    asyncio.run(main())