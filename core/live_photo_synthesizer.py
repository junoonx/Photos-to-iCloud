#!/usr/bin/env python3
"""
Live Photo Pairing & Synthesizer Engine
Author: Antigravity and junoonx
Description: Re-links disjoint Google Photos Takeout still images and video clips into
             native Apple Live Photos via QuickTime Content Identifier metadata.
"""

import os
import sys
import uuid
import subprocess

class LivePhotoSynthesizer:
    def __init__(self, exiftool_path=None):
        if exiftool_path and os.path.exists(exiftool_path):
            self.exiftool_path = exiftool_path
        elif getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            self.exiftool_path = os.path.join(sys._MEIPASS, "bin", "exiftool")
        else:
            local_bin = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bin", "exiftool"))
            self.exiftool_path = local_bin if os.path.exists(local_bin) else "/usr/local/bin/exiftool"

    def get_content_identifier(self, file_path):
        """Checks if a file already has an Apple/QuickTime ContentIdentifier."""
        try:
            cmd = [self.exiftool_path, '-s3', '-ContentIdentifier', file_path]
            res = subprocess.run(cmd, capture_output=True, text=True)
            return res.stdout.strip() or None
        except Exception:
            return None

    def pair_still_and_video(self, image_path, video_path):
        """
        Injects a shared unique UUID ContentIdentifier into both image and video
        so Apple Photos recognizes them as a single native Live Photo.
        """
        if not os.path.exists(image_path) or not os.path.exists(video_path):
            return False, "File not found"
            
        img_cid = self.get_content_identifier(image_path)
        vid_cid = self.get_content_identifier(video_path)
        
        if img_cid and vid_cid and img_cid == vid_cid:
            return True, f"Already paired (UUID: {img_cid})"
            
        shared_uuid = img_cid or vid_cid or str(uuid.uuid4()).upper()
        
        # Inject into image
        img_cmd = [
            self.exiftool_path,
            '-overwrite_original',
            '-m',
            f'-Apple:ContentIdentifier={shared_uuid}',
            f'-MakerNotes:ContentIdentifier={shared_uuid}',
            image_path
        ]
        res_img = subprocess.run(img_cmd, capture_output=True, text=True)
        
        # Inject into video
        vid_cmd = [
            self.exiftool_path,
            '-overwrite_original',
            '-m',
            '-api', 'largefilesupport=1',
            f'-QuickTime:ContentIdentifier={shared_uuid}',
            f'-Keys:ContentIdentifier={shared_uuid}',
            video_path
        ]
        res_vid = subprocess.run(vid_cmd, capture_output=True, text=True)
        
        if res_img.returncode == 0 and res_vid.returncode == 0:
            return True, f"Successfully paired as Live Photo (UUID: {shared_uuid})"
        else:
            return False, f"Failed to inject UUID: {res_img.stderr} | {res_vid.stderr}"

    def find_potential_live_photos_in_directory(self, dir_path):
        """Finds candidate still + movie pairs in the directory."""
        candidates = []
        try:
            files = os.listdir(dir_path)
            stills = {}
            movies = {}
            for f in files:
                if f.startswith('._'):
                    continue
                name, ext = os.path.splitext(f)
                ext_l = ext.lower()
                full_p = os.path.join(dir_path, f)
                if ext_l in ('.jpg', '.jpeg', '.heic'):
                    stills[name] = full_p
                elif ext_l in ('.mov', '.mp4'):
                    movies[name] = full_p
                    
            for base_name, img_path in stills.items():
                if base_name in movies:
                    candidates.append((img_path, movies[base_name]))
        except Exception:
            pass
        return candidates

if __name__ == '__main__':
    synth = LivePhotoSynthesizer()
    print("Live Photo Synthesizer ready. Exiftool:", synth.exiftool_path)
