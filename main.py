
# from Maurice import Client

from tsbot import TSBot
from utils import ZmqRelay
import re
import threading 
import time
import random
from pprint import pprint
import coloredlogs, logging
import json
import base64

logger = logging.getLogger(__name__)
coloredlogs.install(level='INFO', logger=logger)

lock = threading.Lock()

bot = TSBot()
bot.hashtag_map = bot.config['HASHTAG_MAP']
bot.link_map = bot.config['LINK_MAP']
bot.link_map_list = list( bot.link_map.keys() )

twitter_sender = ZmqRelay('twitter', singular=True)
pulse_sender = ZmqRelay('bfxpulse', singular=True)
tvchartinfo = ZmqRelay( 'tvchartinfo' )

chart_posters = {} 

def handle_incoming_messages(bot):
	while True:
		try:
			logger.info('Waiting for tv chart message')
			msg = tvchartinfo.receiver.recv()
			logger.info('Received tv chart message')
			topic, resp = tvchartinfo.demogrify(msg.decode("utf-8"))
		except Exception as e:
			msg = "Failed to receive a reply from tvchartinfo bot"
			bot.ts3conn.sendtextmessage(targetmode=2, target=1, msg=msg)
			print(msg)
			continue  

		id_ = resp['id']
		post = chart_posters[id_]
		data = post['data'] 

		if 'error' in resp:
			with lock:
				logger.info('Locked and sending message to teamspeak: {}'.format(reply['error']))
				bot.ts3conn.sendtextmessage(targetmode=2, target=1, msg=resp['error'])
			logger.critical(resp['error'])
			continue   

		resp['exchange_link'] = bot.link_map[random.choice(bot.link_map_list)]

		if 'exchange' in resp:
			if resp['exchange'] in bot.link_map:
				resp['exchange_link'] = bot.link_map[resp['exchange']]
		else:
			resp['exchange'] = '' 
			resp['price'] = '' 

		reply = "{} posted [b][url=http://{}]{}[/url]:{}:{} at {}[/b]".format(
		 		data['invokername'], resp['exchange_link'], resp['exchange'], resp['ticker'], resp['timeframe_formatted'], resp['price'],
		)
		pprint('replying: {}'.format(reply))
		with lock:
			logger.info('Locked and sending message to teamspeak: {}'.format(reply))
			bot.ts3conn.sendtextmessage(targetmode=2, target=1, msg=reply)


		# Make hashtag selection list
		ht_selection = [ resp['ticker'].lower(), resp['ticker'].upper(), ]
		if resp['ticker'] in bot.hashtag_map:
			ht_selection = ht_selection + bot.hashtag_map[resp['ticker']] 

		pprint(ht_selection)

		tag_type = random.choice( ['$','#'] )
		resp['hashtag'] = tag_type+random.choice(ht_selection)

		resp['title'] = "{}:{}:{} @ {}".format( resp['exchange'], resp['ticker'], resp['timeframe_formatted'], resp['price'] )
		message_twitter = "{}\n{}\n{}\n\n{}".format( resp['title'], data['txt_msg'], resp['hashtag'], resp['exchange_link']  )

		pprint(message_twitter)

		logger.info('sending to twitter')
		twdata = { 
			'msg': message_twitter,
			'picture': resp['thumb_small_fpath'] 
		}

		twitter_sender.send_msg( twdata )


		with open(resp['input_tv_chart_fpath'], "rb") as image_file:
			img = base64.b64encode(image_file.read())
			img = img.decode("utf-8")
			img = "data:image/png;base64,"+img

			data =  {     
				'title': 'Teamspeak chart: '+resp['title'],    
				'content': "{}\n\n{}".format( data['txt_msg'], resp['hashtag'] ),   
				'isPublic': 1, 
				'isPin': 0, 
				'attachments': [img]
				# 'attachments': []
			}

			pulse_sender.send_msg(data)




		with lock:
			logger.info('Deleting {} from chart_posters'.format(id_))
			del chart_posters[id_]




thread = threading.Thread(target=handle_incoming_messages, args=[bot])
thread.start()


from collections import OrderedDict
event_log = OrderedDict(json.load( open( "event.logformat" ) ))
def log_to_eventlog(data):
	#  logger.info(bot.parse_log(data))
	action = data['action']
	if action not in event_log: 
		event_log[action] = {}

	# Check supplied params are in the event log
	for k,v in data['event_parsed'].items():
		string_type = str(type(v)) 
		if k not in event_log[action]:
			event_log[action][k] = {
				'type':[string_type], 
				'example':[v], 
				'cnt':1
			}
		else:
			t = event_log[action][k]
			event_log[action][k]['cnt'] += 1 

			if string_type not in t['type']:
				event_log[action][k]['type'].append(string_type)
				event_log[action][k]['example'].append(v)


	json.dump( event_log, open( "event.logformat", 'w' ) )


# @bot.on('keep_alive_ping')
@bot.on('notifyclientleftview')
@bot.on('notifycliententerview')
@bot.on('notifyclientmoved') # DONE 
@bot.on('notify_channel_edited')
def do_something(data):
	log_to_eventlog(data) 

@bot.on('notifytextmessage')
def do_soomething_else(data):
	log_to_eventlog(data)
	data = data['event_parsed']

	# Look for TV Links in the message 
	regex_query = "(https?:\/\/www\.tradingview\.com\/[A-Za-z]\/[A-Za-z0-9\/]+)"
	results = re.findall(r""+regex_query, data['txt_msg'])

	if len(results) >= 1:
		post = {'id': time.time_ns(), 'url': results[0] }
		tvchartinfo.send_msg( post )
		with lock: 
			chart_posters[ post['id'] ] = {
				'data': data,
				'post_id': post['id'],
				'url': post['url']
			}

	# Look for '!seen' command
	regex_query = "(^!seen) ([A-Za-z0-9_\-!]+)"
	results = re.findall(r""+regex_query, data['txt_msg'])
	if len(results) >= 1:
		username = results[0][1]
		regex_query = "({}+)".format(username)

		reply = None

		for c in reversed(bot.client_list):

			node = bot.client_list[c]
			matches = re.search(regex_query, node['client_nickname'], re.IGNORECASE)
			# if node['name'] == username:
			if matches is not None:
				# pprint(regex_query)
				# pprint(node['name'])
				# pprint( re.search(regex_query, node['name'], re.IGNORECASE) ) 

				reply = "{} @ [i]{}[/i]".format(node['log_parsed_bb'], node['log_time'].strftime("%Y-%m-%d %H\:\%M"))
				bot.ts3conn.sendtextmessage(targetmode=2, target=1, msg=reply)
				return 

		if reply == None:
			bot.ts3conn.sendtextmessage(targetmode=2, target=1, msg='Unable to find any record of {}'.format(username))


	# Look for '!uptime' command
	regex_query = "(^!uptime)"
	match = re.search(regex_query, data['msg'], re.IGNORECASE)
	if match is not None: 
		uptime = time.time() - bot.start_time

		def sec_to_hours(seconds):
			a = seconds//3600
			b = (seconds%3600)//60
			c = (seconds%3600)%60
			return "{} hours {} mins {} seconds".format(int(round(a)), int(round(b)), int(round(c)))

		reply = "I've been running [b]{}[/b]".format(sec_to_hours(uptime))

		bot.ts3conn.sendtextmessage(targetmode=2, target=1, msg=reply)



bot.run()

