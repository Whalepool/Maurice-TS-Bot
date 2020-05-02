import asyncio
from threading import Thread, Lock
from utils import LoadYamlConfig, ZmqRelay
from pyee import EventEmitter
import sys
import signal
import coloredlogs, logging
import ts3
from ts3.escape import TS3Escape
import string
import random
import threading 
from datetime import datetime
from pprint import pprint
import bbcode
import time
import re
logger = logging.getLogger(__name__)
coloredlogs.install(level='DEBUG', logger=logger)

class TSBot( LoadYamlConfig ):

	def __init__(self, create_event_emitter=None):

		self.start_time = time.time()

		# Load config
		LoadYamlConfig.__init__(self)

		self.hashtag_map = self.config['HASHTAG_MAP']
		self.link_map = self.config['LINK_MAP']
		self.link_map_list = list( self.link_map.keys() )

		# Event handlers
		self.events = EventEmitter(scheduler=asyncio.ensure_future)

		# Channel list
		self.channel_list = {} 
		self.max_cname_length = 20

		# Clients list
		self.client_list = {}
		self.max_name_length = 20

		# Text Message parser
		self.txtmsg_parser = bbcode.Parser()

		# Signal handlers
		signal.signal(signal.SIGINT, self.handle_crtl_c)


		logger.info( "Connecting to teamspeak" ) 
		self.ts3conn =  ts3.query.TS3Connection(
				self.config['TS_SERVER_IP'], self.config['TS_SERVER_PORT']
		)
		
		try:
			logger.info( "Logging in to teamspeak" ) 
			self.ts3conn.login(
			client_login_name=self.config['TS_API_USERNAME'],
			client_login_password=self.config['TS_API_PASSWORD']
			)
		except ts3.query.TS3QueryError as err:
			logger.error("Login failed:", err.resp.error["msg"])
			exit(1)

		logger.info("..connected")
		self.ts3conn.use(sid=self.config['TS_SERVER_ID'])
		self.ts3conn.clientupdate(client_nickname=self.config['TS_BOT_USERNAME']+self.id_generator(6))

		channel_list = self.ts3conn.channellist()
		channel_list = channel_list.parsed
		self.update_channel_list( channel_list ) 
		self._emit('channel_list_refresh', self.channel_list)

		client_list = self.ts3conn.clientlist()
		client_list = client_list.parsed
		self.update_client_list( client_list ) 
		self._emit('client_list_refresh', self.channel_list)

		resp = self.ts3conn.whoami()
		room_id = self.config['TS_PRIMARY_ROOM_ID']
		client_id=resp[0]['client_id']
		self.ts3conn.clientmove(cid=room_id, clid=client_id)

		self.ts3conn.servernotifyregister(event='server')
		self.ts3conn.servernotifyregister(event='channel', id_=self.config['TS_PRIMARY_ROOM_ID'])
		self.ts3conn.servernotifyregister(event='textserver', id_=self.config['TS_PRIMARY_ROOM_ID'])
		self.ts3conn.servernotifyregister(event='textchannel', id_=self.config['TS_PRIMARY_ROOM_ID'])
		self.ts3conn.servernotifyregister(event='textprivate', id_=self.config['TS_PRIMARY_ROOM_ID'])

		def listen_for_ts3_events():

			while True:
				try:
					event = self.ts3conn.wait_for_event()
				except ts3.query.TS3TimeoutError:
					pass

				else:

					message_types = [
						'notifyclientleftview',  # Client leaves
						'notifycliententerview', # Client joins 
						'notifytextmessage',		 # Text message 
						'notifyclientmoved', 		 # Client moved channel
						'notifychanneledited', 	 # Edited channel
					]
					event._parse_data()
					event_data_as_string = TS3Escape.unescape(event._data[0].decode("utf-8"))
					(action, rest) = event_data_as_string.split(maxsplit=1)

					data = {
						'action': action,
						'event_raw': event._data[0],
						'event_parsed': event._parsed[0],
					}
					if action in message_types:
						func = 'action_'+action
					else:
						func = 'action_unknown'

					getattr(self, func)(data)


		threading.Thread(target=listen_for_ts3_events, args=(), daemon=True).start()


	def action_unknown(self, data):
		pprint(data) 


	def action_notifyclientleftview(self, data):
		data = data['event_parsed']
		data['clid'] = int(data['clid'])
		data['user'] = self.client_list[ data['clid'] ]
		data['log_time'] = datetime.utcnow()
		data['log'] = ">>> Disconnected: {username_short}, {reason}"
		# .format(data['user']['name'], data['reasonmsg'])

		# Reset the last channel they were in
		self.client_list[ data['clid'] ]['cid'] = None

		self.client_list[ data['clid'] ]['log_time'] = data['log_time']
		self.client_list[ data['clid'] ]['log'] = data['log']
		self.client_list[ data['clid'] ]['log_parsed_bb'] = self.parse_log( data, bbcode=True )

		self._emit('notify_client_disconnected', data)


	def action_notifycliententerview(self, data):
		data = data['event_parsed']
		data['clid'] = int(data['clid'])
		data['log_time'] = datetime.utcnow()
		data['log'] = ">>> Connected: {username_short}"
	

		if data['clid'] not in self.client_list:
			self.client_list[data['clid']] = { 
				'name': data['client_nickname'],
				'database_id': int(data['client_database_id']),
				'cid': int(data['ctid']),
				'clid': data['clid'],
			}

		data['user'] = self.client_list[ data['clid'] ]

		self.client_list[ data['clid'] ]['log_time'] = data['log_time']
		self.client_list[ data['clid'] ]['log'] = data['log']
		self.client_list[ data['clid'] ]['log_parsed_bb'] = self.parse_log( data, bbcode=True )

		self._emit('notify_client_connected', data)



	def action_notifytextmessage(self, data):
		data = data['event_parsed']
		if 'invokerid' in data: 
			data['clid'] = int(data['invokerid'])
		else:
			data['clid'] = int(data['clid'])
		data['user'] = self.client_list[ data['clid'] ]

		data['channel'] = self.channel_list[ data['user']['cid'] ]
		data['txt_msg'] = self.txtmsg_parser.strip(data['msg'])
		data['log_time'] = datetime.utcnow()
		data['log'] = "{username} in {curr_channel} said: {text_msg}"

		self.client_list[ data['clid'] ]['log_time'] = data['log_time']
		self.client_list[ data['clid'] ]['log'] = data['log']
		self.client_list[ data['clid'] ]['log_parsed_bb'] = self.parse_log( data, bbcode=True )

		self._emit('notify_text_message', data)



	def action_notifyclientmoved(self, data):
		data = data['event_parsed']
		data['clid'] = int(data['clid'])
		data['user'] = self.client_list[ data['clid'] ]
		data['to_channel'] = self.channel_list[ int(data['ctid']) ]
		data['log_time'] = datetime.utcnow()

		# already in a channel
		if data['user']['cid'] is not None:
			data['from_channel'] = self.channel_list[ data['user']['cid'] ]
			log = '{username_short} moved from {from_channel} to {to_channel}'
		else:
			log = "{username_short} moved to {to_channel}"

		data['log'] = log

		self.client_list[ data['clid'] ]['cid'] = int(data['ctid'])
		self.client_list[ data['clid'] ]['log_time'] = data['log_time']
		self.client_list[ data['clid'] ]['log'] = log
		self.client_list[ data['clid'] ]['log_parsed_bb'] = self.parse_log( data, bbcode=True )


		self._emit('notify_client_moved', data)


	def action_notifychanneledited(self, data):
		data = data['event_parsed']
		data['clid'] = int(data['invokerid'])
		data['user'] = self.client_list[ data['clid'] ]
		data['channel'] = self.channel_list[ data['user']['cid'] ]
		data['eddited_cname'] = data['channel_name']
		data['log_time'] = datetime.utcnow()
		data['log'] = "{username_short} edited {curr_channel} to {eddited_cname}"

		# Update the channel name in the names list
		self.channel_list[ data['user']['cid'] ]['name'] = data['channel_name']
		self.client_list[ data['clid'] ]['log_parsed_bb'] = self.parse_log( data, bbcode=True )

		self._emit('notify_channel_edited', data)



	def parse_log(self, data, bbcode=False):
		end_c = '\033[0m'
		green = '\u001b[38;5;154m'
		green_end = '\033[0m'
		purple = '\u001b[38;5;141m'
		blue = '\u001b[38;5;123m'
		red = '\u001b[38;5;196m' 
		custom = '\u001b[38;5;1'+str(data['clid'])[-2]+str(data['clid'])[-1]+'m'

		if bbcode == True: 
			end_c = '[/color]'
			green = '[color=#336600]'
			purple = '[color=#660066]'
			blue = '[color=#0033cc]'
			red = '[color=#cc0000]' 
			custom = '[color=#003300]'

		username = "{}{}{}".format(green, data['user']['name'], end_c)
		username_short = username 
		username = ("{:<"+str(self.max_name_length)+"}").format(username)

		curr_channel = ''
		if data['user']['cid'] is not None:
			data['channel'] = self.channel_list[ data['user']['cid'] ]
			curr_channel = "{}{}{}".format(purple, data['channel']['name'], end_c)
			curr_channel = ("{:<"+str(self.max_cname_length)+"}").format(curr_channel)

		from_channel = ''
		if 'from_channel' in data:
			from_channel = "{}{}{}".format(blue, data['from_channel']['name'] , end_c)
			from_channel = ("{:<"+str(self.max_cname_length)+"}").format(from_channel)


		to_channel = ''
		if 'to_channel' in data:
			to_channel = "{}{}{}".format(blue, data['to_channel']['name'] , end_c)
			to_channel = ("{:<"+str(self.max_cname_length)+"}").format(to_channel)

		eddited_cname = ''
		if 'eddited_cname' in data:
			eddited_cname = "{}{}{}".format(blue, data['eddited_cname'], end_c)
			eddited_cname = ("{:<"+str(self.max_cname_length)+"}").format(eddited_cname)

		reason = '' 
		if 'reasonmsg' in data:
			reason = "{}{}{}".format(red, data['reasonmsg'], end_c)

		text_msg = '' 
		if 'txt_msg' in data:		
			text_msg = "{}{}{}".format(custom, data['txt_msg'], end_c)


		out = data['log'].format(
						username 			= username, 
						username_short = username_short,
						curr_channel	= curr_channel,
						from_channel  = from_channel,
						to_channel		= to_channel,
						eddited_cname = eddited_cname,
						reason 				=	reason,
						text_msg 			= text_msg,
			)
		return out 


	def id_generator(self, size=6, chars=string.ascii_uppercase + string.digits):
		return ''.join(random.choice(chars) for _ in range(size))


	def handle_crtl_c(self, sig, frame):
		logger.error('Closing connection & quitting')
		self.ts3conn.quit()
		sys.exit(0)


	def on(self, event, func=None):
		if not func:
		    return self.events.on(event)
		self.events.on(event, func)


	def _emit(self, event, *args, **kwargs):
		self.events.emit(event, *args, **kwargs)

	def get_client(self, clid, c):
		clid = int(clid)
		if clid in self.client_list:
			return self.client_list[clid]
		else:
			if 'cid' in c:
				cid = int(c['cid'])
			elif 'tcid' in c:
				cid = int(c['tcid'])
			else
				cid = None 

			self.client_list[ clid ] = {
				'name': c['client_nickname'],
				'database_id': int(c['client_database_id']),
				'cid': cid,
				'clid': clid,
				'log_time': datetime.utcnow(),
				'log': 'saw {} '.format(c['client_nickname']),
				'log_parsed_bb': 'saw [color=#336600]{}[/color] in [color=#660066]{}[/color]'.format(c['client_nickname'])
			}
			if len(c['client_nickname']) > self.max_name_length:
				self.max_name_length = len(c['client_nickname'])

	def update_client_list(self, client_list):
		self.client_list = {}
		for c in client_list:
			self.client_list[ int(c['clid']) ] = {
				'name': c['client_nickname'],
				'database_id': int(c['client_database_id']),
				'cid': int(c['cid']),
				'clid': int(c['clid']),
				'log_time': datetime.utcnow(),
				'log': 'saw {} in {} when I connected '.format(c['client_nickname'], self.channel_list[int(c['cid'])]['name']),
				'log_parsed_bb': 'saw [color=#336600]{}[/color] in [color=#660066]{}[/color] when I connected '.format(c['client_nickname'], self.channel_list[int(c['cid'])]['name'])
			}
			if len(c['client_nickname']) > self.max_name_length:
				self.max_name_length = len(c['client_nickname'])


	def update_channel_list(self, channel_list):
		self.channel_list = {} 
		for c in channel_list:
			self.channel_list[ int(c['cid']) ] = {
				'name': c['channel_name'],
				'needed_subscriber_power': int(c['channel_needed_subscribe_power']),
				'order': int(c['channel_order']),
				'cid': int(c['cid']),
				'pid': int(c['pid']),
			}
			if len(c['channel_name']) > self.max_cname_length:
				self.max_cname_length = len(c['channel_name'])

	async def _keep_alive(self):
		while True:
			await asyncio.sleep(60)
			self.ts3conn.send_keepalive()
			self._emit('keep_alive_ping','keep_alive_ping')
			

	def run(self):
		def start_loop(loop):
			asyncio.set_event_loop(loop)
			loop.run_until_complete(self._keep_alive())

		worker_loop = asyncio.new_event_loop()
		worker = Thread(target=start_loop, args=(worker_loop,))
		worker.start()