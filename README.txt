AI NEWS VIDEO BOT

Put credentials.json beside video_bot.py.
Create .env from .env.example and put your OpenRouter key there.
Install requirements, install FFmpeg, then run python video_bot.py.
First run opens Google OAuth and creates token.json.
The bot uses spreadsheet "title ai payton", sheet "News", and processes one PENDING row per run.
It fetches the article, asks OpenRouter for factual Arabic script/scenes, creates Arabic TTS, builds an MP4 with source images, uploads it to Google Drive folder AI NEWS VIDEOS/VIDEOS, and writes the Drive link to column N and status to column L.
Do not share credentials.json, token.json, or .env.
