### Maurice Teamspeak Bot
   
Built ontop of [py-ts3](https://github.com/benediktschmitt/py-ts3)  
The 'bot' code (`tsbot.py`):
- Joins server, logs in, joins a room 
- subscribes to all events
- build/maintains a client and channel list   

Used in `main.py` for: 
- Collecting tradingview charts posted into the chat, posting them to twitter also etc
- Has a !seen [user] and !uptime command also

Overall a fun little project. 


### Usage 
```
python main.py
```  

Cron script to keep charts folder clean
```
find /path/to/Maurice/charts/ -mindepth 1 -mmin +10 -delete
```

### Events
```python
@bot.on('channel_list_refresh')
@bot.on('client_list_refresh')
@bot.on('keep_alive_ping')
@bot.on('notify_client_disconnected')
@bot.on('notify_client_connected')
@bot.on('notify_client_moved')
@bot.on('notify_channel_edited')
@bot.on('notify_text_message')
```

![Maurice](https://i.imgur.com/tT76fc9.jpg)  

Terminal output 
![Terminal Output](https://i.imgur.com/eQVuxKO.png)
