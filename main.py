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
TEMP_DIR = "temp"
TEMP_AUDIO = os.path.join(TEMP_DIR, "temp_audio.mp3")
TEMP_WAV = os.path.join(TEMP_DIR, "temp_audio.wav") # Used for stable ffmpeg rendering
TEMP_SUBS = os.path.join(TEMP_DIR, "temp_subs.ass")
ASSETS_DIR = "assets"
BG_VIDEO = os.path.join(ASSETS_DIR, "sub.mp4")

# --- STYLE SETTINGS ---
FONT = "Arial Rounded MT Bold" 
FONT_SIZE = 80            
OUTLINE_WIDTH = 4         

# Colors
COLOR_WHITE = "&H00FFFFFF&"
COLOR_YELLOW = "&H0000FFFF&" 
COLOR_BLACK = "&H00000000&"

class BrainrotGenerator:
    def __init__(self, background_path=BG_VIDEO):
        self.background_path = background_path

    async def generate_audio_and_data(self, text):
        """
        Generates MP3, extracts timestamps, and CONVERTS to WAV.
        """
        # TEST_VOICE = "en-US-AriaNeural" # Use Aria if Guy has timestamp issues
        TEST_VOICE = "en-US-GuyNeural"
        print(f"🎤 Generating TTS with voice: {TEST_VOICE}...")
        
        communicate = edge_tts.Communicate(text, TEST_VOICE)
        word_data = []
        
        # 1. Generate MP3
        with open(TEMP_AUDIO, "wb") as file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    file.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    word_data.append({
                        "word": chunk["text"],
                        "start": chunk["offset"] / 10_000_000,
                        "end": (chunk["offset"] + chunk["duration"]) / 10_000_000
                    })
        
        print(f"✅ Extracted {len(word_data)} words.")

        # 2. Convert to WAV (Fixes audio cutoff issues)
        print("🔄 Converting MP3 to WAV for video stability...")
        subprocess.run([
            "ffmpeg", "-y", "-i", TEMP_AUDIO, 
            "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", 
            TEMP_WAV
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if len(word_data) == 0:
            print("⚠️ WARNING: No timing data received. Using fallback.")
            return self.fallback_timing(text)
            
        return word_data

    def fallback_timing(self, text):
        words = text.split()
        if not words: return []
        try:
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", TEMP_WAV]
            duration = float(subprocess.check_output(cmd).strip())
        except:
            duration = len(words) * 0.5 
            
        time_per_word = duration / len(words)
        fallback_data = []
        current_time = 0.0
        for w in words:
            fallback_data.append({
                "word": w, "start": current_time, "end": current_time + time_per_word
            })
            current_time += time_per_word
        return fallback_data

    def chunk_words(self, words, max_chars=22): # Slightly smaller chunks for better vertical fit
        chunks = []
        current_chunk = []
        current_len = 0
        for w in words:
            word_len = len(w['word'])
            if current_len + word_len > max_chars and current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                current_len = 0
            current_chunk.append(w)
            current_len += word_len + 1 
        if current_chunk:
            chunks.append(current_chunk)
        return chunks

    def generate_ass_subtitles(self, word_data):
        """
        Generates ASS with fixed positioning and seamless transitions.
        """
        # Alignment 5 = Top Center (legacy) or Center. We force position with \pos anyway.
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{FONT},{FONT_SIZE},{COLOR_WHITE},{COLOR_BLACK},{COLOR_BLACK},{COLOR_BLACK},-1,0,1,{OUTLINE_WIDTH},2,5,10,10,500,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events = []
        chunks = self.chunk_words(word_data, max_chars=20) 
        
        for c_idx, chunk in enumerate(chunks):
            # --- LOGIC TO BRIDGE THE GAP ---
            # Determine exactly when this Chunk page should disappear.
            if c_idx < len(chunks) - 1:
                # If there is a next chunk, this chunk stays until the exact moment the next one starts.
                # This prevents the gap/blinking effect.
                chunk_end_time = chunks[c_idx + 1][0]['start']
            else:
                # If it's the last chunk, hold it for 1 second after speech ends.
                chunk_end_time = chunk[-1]['end'] + 1.0

            for i, active_word in enumerate(chunk):
                start_t = active_word['start']
                
                # Determine when the HIGHLIGHT moves (not the text itself)
                if i < len(chunk) - 1:
                    end_t = chunk[i+1]['start']
                else:
                    # If it's the last word in this chunk, the highlight stays
                    # until the chunk is replaced by the next chunk.
                    end_t = chunk_end_time

                # Build the text with Highlight tags
                line_parts = []
                for w in chunk:
                    text = w['word'].replace('{', '\\{').replace('}', '\\}')
                    if w == active_word:
                        line_parts.append(f"{{\\1c{COLOR_YELLOW}}}{text}{{\\1c{COLOR_WHITE}}}")
                    else:
                        line_parts.append(text)
                
                final_text = " ".join(line_parts)
                
                # --- POSITION LOCK ---
                # \an5 = Align Center
                # \pos(540, 960) = Exact center of 1080x1920 canvas.
                # This ensures the text never jumps up/down.
                final_text = f"{{\\an5}}{{\\pos(540,960)}}{final_text}"

                ass_start = self._format_time(start_t)
                ass_end = self._format_time(end_t)
                events.append(f"Dialogue: 0,{ass_start},{ass_end},Default,,0,0,0,,{final_text}")

        print(f"📝 Generated {len(events)} subtitle events.")
        with open(TEMP_SUBS, "w", encoding="utf-8") as f:
            f.write(header + "\n".join(events))
            
        return TEMP_SUBS

    def _format_time(self, seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        cs = int((seconds - int(seconds)) * 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    def render_video(self):
        if not os.path.exists(self.background_path):
            print(f"❌ Error: Background video not found at {self.background_path}")
            return

        print("🎬 Rendering Final Video with FFmpeg...")
        subs_path = os.path.abspath(TEMP_SUBS).replace('\\', '/').replace(':', '\\:')
        
        # Use TEMP_WAV (Input 1) to ensure audio length is detected correctly
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",       
            "-i", self.background_path, # [0] Video
            "-i", TEMP_WAV,             # [1] Audio (WAV)
            "-vf", f"crop=ih*(9/16):ih:(iw-ow)/2:0,ass='{subs_path}'", 
            "-map", "0:v",              
            "-map", "1:a",              
            "-shortest",                
            "-c:v", "libx264", "-preset", "ultrafast",     
            "-c:a", "aac", "-b:a", "192k",             
            OUTPUT_FILE
        ]
        
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if process.returncode != 0:
            print(f"❌ FFmpeg Error:\n{process.stderr}")
        else:
            print(f"✅ Success! Video saved to: {OUTPUT_FILE}")

    async def run(self, text):
        if not os.path.exists(TEMP_DIR): os.makedirs(TEMP_DIR)
        if not os.path.exists("output"): os.makedirs("output")
        
        # 1. Generate Data & Audio
        word_data = await self.generate_audio_and_data(text)
        if not word_data: return

        # 2. Generate Subtitles
        self.generate_ass_subtitles(word_data)
        
        # 3. Render
        self.render_video()

if __name__ == "__main__":
    if not os.path.exists(ASSETS_DIR):
        os.makedirs(ASSETS_DIR)
        print(f"⚠️ Created '{ASSETS_DIR}'. Put 'sub.mp4' inside.")
        sys.exit(1)
        
    if len(sys.argv) > 1:
        user_text = " ".join(sys.argv[1:])
    else:
        user_text = "The mitochondrial powerhouse of the cell is not just a meme, it is a biological reality. We are going to learn about biology today."
    
    # Uppercase for Brainrot aesthetic
    user_text = user_text.upper()
    
    generator = BrainrotGenerator()
    asyncio.run(generator.run(user_text))