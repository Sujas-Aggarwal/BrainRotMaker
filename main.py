import asyncio
import edge_tts
import os
import subprocess
import json
import math
import sys
import textwrap

# --- CONFIGURATION ---
VOICE = "en-US-GuyNeural"
OUTPUT_FILE = "output/brainrot_final.mp4"
TEMP_AUDIO = "temp/temp_audio.mp3"
TEMP_SUBS = "temp/temp_subs.ass"
TEMP_BG = "temp/temp_bg_cropped.mp4"
ASSETS_DIR = "assets"
BG_VIDEO = os.path.join(ASSETS_DIR, "sub.mp4")

# ASS Styling
FONT = "Arial"
FONT_SIZE = 70
# Colors in ASS are &HBBGGRR (Blue, Green, Red)
COLOR_WHITE = "&H00FFFFFF&"
COLOR_YELLOW = "&H0000FFFF&"  # R=FF, G=FF, B=00
COLOR_BLACK = "&H00000000&"
OUTLINE_WIDTH = 3

class BrainrotGenerator:
    def __init__(self, background_path=BG_VIDEO):
        self.background_path = background_path

    async def generate_audio_and_data(self, text):
        """
        Generates MP3 and extracts timestamps. 
        Includes DEBUG prints to verify what data we are receiving.
        """
        # Switch to AriaNeural temporarily to test (it is more reliable for timestamps)
        # TEST_VOICE = "en-US-AriaNeural" 
        TEST_VOICE = "en-US-GuyNeural"
        print(f"🎤 Generating TTS with voice: {TEST_VOICE}...")
        
        communicate = edge_tts.Communicate(text, TEST_VOICE)
        word_data = []
        
        with open(TEMP_AUDIO, "wb") as file:
            async for chunk in communicate.stream():
                # DEBUG: Print the first few keys of any chunk to see what we get
                # print(f"Received chunk type: {chunk.get('type')}") 
                
                if chunk["type"] == "audio":
                    file.write(chunk["data"])
                
                elif chunk["type"] == "WordBoundary":
                    # Debug print to confirm we found a boundary
                    # print(f"Found word: {chunk['text']}")
                    
                    word_data.append({
                        "word": chunk["text"],
                        "start": chunk["offset"] / 10_000_000,
                        "end": (chunk["offset"] + chunk["duration"]) / 10_000_000
                    })
        
        print(f"✅ Extracted {len(word_data)} words.")
        
        # If still empty, the text might be too short or weirdly formatted.
        if len(word_data) == 0:
            print("⚠️ WARNING: No timing data received. Using fallback timing.")
            return self.fallback_timing(text)
            
        return word_data

    def fallback_timing(self, text):
        """
        Fallback: If API fails to give timestamps, we estimate them evenly.
        This prevents the video from crashing/being empty.
        """
        import audio_metadata # or use os.path.getsize to estimate duration
        
        # Estimate duration from file size (approx 1 second = 4kb for standard tts mp3 usually, but inconsistent)
        # Better: use the file we just wrote
        words = text.split()
        if not words: return []
        
        # Use FFmpeg to get exact duration of the audio file we just saved
        try:
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", TEMP_AUDIO]
            duration = float(subprocess.check_output(cmd).strip())
        except:
            duration = len(words) * 0.5 # Fail safe guess
            
        time_per_word = duration / len(words)
        
        fallback_data = []
        current_time = 0.0
        for w in words:
            fallback_data.append({
                "word": w,
                "start": current_time,
                "end": current_time + time_per_word
            })
            current_time += time_per_word
            
        return fallback_data

    def chunk_words(self, words, max_chars=30):
        """
        Groups words into 'pages' or 'lines' so they appear together.
        Example: [a, b, c, d, e, f] -> [[a, b, c], [d, e, f]]
        """
        chunks = []
        current_chunk = []
        current_len = 0
        
        for w in words:
            word_len = len(w['word'])
            # If adding this word exceeds max_chars, start a new chunk
            # (unless it's the very first word of a chunk)
            if current_len + word_len > max_chars and current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                current_len = 0
            
            current_chunk.append(w)
            current_len += word_len + 1 # +1 for space
            
        if current_chunk:
            chunks.append(current_chunk)
        return chunks

    def generate_ass_subtitles(self, word_data):
        """
        Generates the .ass file with "Paginated Karaoke" logic.
        Logic: The whole chunk is visible, but the active word changes color.
        """
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{FONT},{FONT_SIZE},{COLOR_WHITE},{COLOR_BLACK},{COLOR_BLACK},{COLOR_BLACK},1,0,1,{OUTLINE_WIDTH},0,5,10,10,500,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events = []
        
        # 1. Group words into static lines/chunks
        chunks = self.chunk_words(word_data, max_chars=25) # Adjust max_chars to fit screen width
        
        for chunk in chunks:
            # For every word in this chunk, we create an event.
            # The text remains the same (the whole chunk), but the highlighted word changes.
            
            for i, active_word in enumerate(chunk):
                # Start time is when this word starts spoken
                start_t = active_word['start']
                
                # End time logic:
                # If there is a next word in THIS chunk, end when next word starts (keeps text on screen)
                # If it's the last word in the chunk, end when the word ends (or slight padding)
                if i < len(chunk) - 1:
                    end_t = chunk[i+1]['start']
                else:
                    end_t = active_word['end'] + 0.1 # Slight padding for last word
                
                # Build the text string for this event
                line_parts = []
                for w in chunk:
                    text = w['word']
                    # Escape special ASS characters if needed
                    text = text.replace('{', '\\{').replace('}', '\\}')
                    
                    if w == active_word:
                        # Highlight Yellow
                        line_parts.append(f"{{\\1c{COLOR_YELLOW}}}{text}{{\\1c{COLOR_WHITE}}}")
                    else:
                        # Normal White
                        line_parts.append(text)
                
                final_text = " ".join(line_parts)
                
                # Create ASS event line
                ass_start = self._format_time(start_t)
                ass_end = self._format_time(end_t)
                events.append(f"Dialogue: 0,{ass_start},{ass_end},Default,,0,0,0,,{final_text}")

        print(f"📝 Generated {len(events)} subtitle events.")
        with open(TEMP_SUBS, "w", encoding="utf-8") as f:
            f.write(header + "\n".join(events))
            
        return TEMP_SUBS

    def _format_time(self, seconds):
        """Converts seconds to ASS format H:MM:SS.cs"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        cs = int((seconds - int(seconds)) * 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    def render_video(self):
        """
        Runs FFmpeg to composite everything.
        - Loops background
        - Crops to 9:16
        - Adds audio (mapped correctly!)
        - Burns subtitles
        """
        if not os.path.exists(self.background_path):
            print(f"❌ Error: Background video not found at {self.background_path}")
            return

        print("🎬 Rendering Final Video with FFmpeg...")
        
        # Windows/Mac path safety for FFmpeg filter
        subs_path = os.path.abspath(TEMP_SUBS).replace('\\', '/').replace(':', '\\:')
        
        cmd = [
            "ffmpeg",
            "-y",                       # Overwrite output
            "-stream_loop", "-1",       # Loop background
            "-i", self.background_path, # [0] Video
            "-i", TEMP_AUDIO,           # [1] Audio
            "-vf",                      # Filter chain
            f"crop=ih*(9/16):ih:(iw-ow)/2:0,ass='{subs_path}'", # Crop center vertical -> add subs
            "-map", "0:v",              # Use Video from [0]
            "-map", "1:a",              # Use Audio from [1] (CRITICAL FIX)
            "-shortest",                # End when audio ends
            "-c:v", "libx264",          # Video Codec
            "-preset", "ultrafast",     # Encoding speed
            "-c:a", "aac",              # Audio Codec
            "-b:a", "192k",             # Audio bitrate
            OUTPUT_FILE
        ]
        
        # Run command
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        if process.returncode != 0:
            print("❌ FFmpeg Error:")
            print(process.stderr)
        else:
            print(f"✅ Success! Video saved to: {OUTPUT_FILE}")

    async def run(self, text):
        # 1. Generate Audio & Timestamps
        word_data = await self.generate_audio_and_data(text)
        if not word_data:
            print("❌ No text data generated.")
            return

        # 2. Generate Subtitles
        self.generate_ass_subtitles(word_data)
        
        # 3. Render
        self.render_video()
        
        # Cleanup
        if os.path.exists(TEMP_AUDIO): os.remove(TEMP_AUDIO)
        if os.path.exists(TEMP_SUBS): os.remove(TEMP_SUBS)

# --- EXECUTION ---
if __name__ == "__main__":
    # Ensure assets folder exists
    if not os.path.exists(ASSETS_DIR):
        os.makedirs(ASSETS_DIR)
        print(f"⚠️ Created '{ASSETS_DIR}' folder. Please place 'sub.mp4' inside it.")
        sys.exit(1)
        
    # Get text from arguments or use default
    if len(sys.argv) > 1:
        user_text = " ".join(sys.argv[1:])
    else:
        user_text = "The mitochondrial powerhouse of the cell is not just a meme, it is a biological reality. We are going to learn about biology today."
        
    generator = BrainrotGenerator()
    asyncio.run(generator.run(user_text))