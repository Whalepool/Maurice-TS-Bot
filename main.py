
# from Maurice import Client

from tsbot import TSBot
from utils import ZmqRelay
import re
import threading 
import time
import random
from pprint import pprint
import coloredlogs, logging

logger = logging.getLogger(__name__)
coloredlogs.install(level='DEBUG', logger=logger)


bot = TSBot()
twitter_sender = ZmqRelay('twitter', singular=True)


# @bot.on('channel_list_refresh')
# @bot.on('client_list_refresh')
# @bot.on('keep_alive_ping')

@bot.on('notify_client_disconnected')
@bot.on('notify_client_connected')
@bot.on('notify_client_moved')
@bot.on('notify_channel_edited')
def somefunc(data):
	logger.info(bot.parse_log(data))


@bot.on('notify_text_message')
def notify_text_message(data):
	logger.info(bot.parse_log(data))

	# Look for TV Links in the message 
	regex_query = "(https?:\/\/www\.tradingview\.com\/[A-Za-z]\/[A-Za-z0-9\/]+)"
	results = re.findall(r""+regex_query, data['txt_msg'])

	if len(results) >= 1:

		tv_link = results[0]
		thread = threading.Thread(target=handle_tv_link_message, args=[bot, tv_link, data], daemon=True)
		thread.start()


	# Look for '!seen' command
	regex_query = "(^!seen) ([A-Za-z0-9_\-!]+)"
	results = re.findall(r""+regex_query, data['txt_msg'])
	if len(results) >= 1:
		username = results[0][1]
		regex_query = "({}.+)".format(username)

		reply = None
		for c in reversed(bot.client_list):

			node = bot.client_list[c]
			matches = re.search(regex_query, node['name'], re.IGNORECASE)

			if matches is not None:
				reply = "{} @ [i]{}[/i]".format(node['log_parsed_bb'], node['log_time'].strftime("%Y-%m-%d %H\:\%M"))
				bot.ts3conn.sendtextmessage(targetmode=2, target=1, msg=reply)
				return 

		if reply == None:
			bot.ts3conn.sendtextmessage(targetmode=2, target=1, msg='Unable to find any record of {}'.format(username))


	# Look for '!uptime' command
	regex_query = "(^!uptime)"
	match = re.search(regex_query, data['txt_msg'], re.IGNORECASE)
	if match is not None: 
		uptime = time.time() - bot.start_time

		def sec_to_hours(seconds):
			a = seconds//3600
			b = (seconds%3600)//60
			c = (seconds%3600)%60
			return "{} hours {} mins {} seconds".format(int(round(a)), int(round(b)), int(round(c)))

		reply = "I've been running [b]{}[/b]".format(sec_to_hours(uptime))

		bot.ts3conn.sendtextmessage(targetmode=2, target=1, msg=reply)


tvchartinfo = ZmqRelay( 'tvchartinfo' )
def handle_tv_link_message(bot, tvlink, data):

	post = {'id': time.time_ns(), 'url': tvlink }
	tvchartinfo.send_msg( post )

	tvchartinfo.set_recv_timeout(10000)
	try:
		msg = tvchartinfo.receiver.recv()
		topic, resp = tvchartinfo.demogrify(msg.decode("utf-8"))

		if 'error' in resp:
			bot.ts3conn.sendtextmessage(targetmode=2, target=1, msg=resp['error'])
			logger.error(resp['error'])
			return  

		resp['exchange_link'] = bot.link_map[random.choice(bot.link_map_list)]

		if 'exchange' in resp:
			if resp['exchange'] in bot.link_map:
				resp['exchange_link'] = bot.link_map[resp['exchange']]
		else:
			resp['exchange'] = '' 
			resp['price'] = '' 
			 
		reply = "{} posted [b][url=http://{}]{}[/url]:{}:{} at {}[/b]".format(
		 		data['user']['name'], resp['exchange_link'], resp['exchange'], resp['ticker'], resp['timeframe_formatted'], resp['price'],
		)
		bot.ts3conn.sendtextmessage(targetmode=2, target=1, msg=reply)

	except Exception as e:
		msg = "Failed to receive a reply from tvchartinfo bot"
		bot.ts3conn.sendtextmessage(targetmode=2, target=1, msg=msg)
		print(msg)
		return 

	# Make hashtag selection list
	ht_selection = [ resp['ticker'].lower(), resp['ticker'].upper(), ]
	if resp['ticker'] in bot.hashtag_map:
		ht_selection = ht_selection + bot.hashtag_map[resp['ticker']] 

	tag_type = random.choice( ['$','#'] )
	resp['hashtag'] = tag_type+random.choice(ht_selection)

	resp['title'] = "{}:{}:{} @ {}".format( resp['exchange'], resp['ticker'], resp['timeframe_formatted'], resp['price'] )
	message_twitter = "{}\n{}\n{}\n\n{}".format( resp['title'], data['txt_msg'], resp['hashtag'], resp['exchange_link']  )

	logger.info('sending to twitter')
	data = { 
		'msg': message_twitter,
		'picture': resp['thumb_small_fpath'] 
	}

	twitter_sender.send_msg( data )






bot.run()

