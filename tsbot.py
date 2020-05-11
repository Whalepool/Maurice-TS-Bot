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

from collections import OrderedDict

logger = logging.getLogger(__name__)
coloredlogs.install(level='INFO', logger=logger)
lock = threading.Lock()


class TSBot( LoadYamlConfig ):

	def __init__(self, create_event_emitter=None):

		self.start_time = time.time()

		# Load config
		LoadYamlConfig.__init__(self)

		# Event handlers
		self.events = EventEmitter(scheduler=asyncio.ensure_future)

		# Channel list
		self.channel_list = OrderedDict()
		self.max_cname_length = 20

		# Clients list
		self.client_list = OrderedDict()
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


		###############################
		# Channels 
		self.build_channels_list()


		###############################
		# Clients 
		self.build_clients_list()


		###############################
		# Server Grouops 
		# ts3cmd.servergroupclientlist(sgid=8, names=True)
		self.server_groups = OrderedDict()
		for sg in self.ts3conn.servergrouplist()._parsed:
			if sg['type'] == '1': 
				self.server_groups[ int(sg['sgid']) ] = OrderedDict({
					'sgid': int(sg['sgid']),
					'iconid': int(sg['iconid']),
					'name': sg['name'],
					'sortid': int(sg['sortid'])
				})
		self.server_groups = OrderedDict(sorted(self.server_groups.items(), key=lambda item: item[1]['sortid']))



		resp = self.ts3conn.whoami()
		room_id = self.config['TS_PRIMARY_ROOM_ID']
		clid=resp[0]['client_id']
		if resp[0]['client_channel_id'] != room_id:
			self.ts3conn.clientmove(cid=room_id, clid=clid)

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

					data = OrderedDict({
						'action': action,
						'event_raw': event._data[0],
						'event_parsed': event._parsed[0],
					})
					if action in message_types:
						func = 'action_'+action
					else:
						func = 'action_unknown'

					getattr(self, func)(data)


		threading.Thread(target=listen_for_ts3_events, args=(), daemon=True).start()


	def action_unknown(self, data):
		logger.critical('Uknown action') 
		pprint(data)

	def action_notifyclientleftview(self, data):
		parsed = data['event_parsed']
		# {'cfid': '44179', 'clid': '31025', 'ctid': '0', 'reasonid': '8', 'reasonmsg': 'leaving'}

		# Client id 
		clid = int(parsed['clid'])

		# User 
		user = self.get_client(clid, allow_short=True)

		# From chan 
		cfid = int(parsed['cfid'])
		from_chan = self.get_channel(cfid)
		self.channel_list[cfid]['total_clients'] -= 1 

		if 'reasonmsg' not in parsed:
			parsed['reasonmsg'] = '' 

		# Update client 
		self.update_client( 
			clid, 
			OrderedDict({
				'log_time': datetime.utcnow(),
				'log_msg': ">>> {username} disconnected from {from_channel}: {reasonmsg}",
				'log_params': {
					'username': user['client_nickname'],
					'from_channel': from_chan['channel_name'],
					'reasonmsg': parsed['reasonmsg'],
				}
			}),
			allow_short=True
		)
		self.update_client(clid, OrderedDict({'log_parsed_bb': self.parse_log( clid, bbcode=True )}),allow_short=True)

		# Output log 
		print( self.parse_log(clid) )

		self._emit('notifyclientleftview', data)


	def action_notifycliententerview(self, data):
		parsed = data['event_parsed']

		# Client id 
		clid = int(parsed['clid'])

		# User 
		user = self.get_client(clid)

		# to chanel
		ctid = int(parsed['ctid'])
		to_chan = self.get_channel(ctid)
		self.channel_list[ ctid ]['total_clients'] += 1 


		# Update client 
		self.update_client( 
			clid, 
			OrderedDict({
				'cid': int(parsed['ctid']), ## Destination channel = parsed['ctid']
				'log_time': datetime.utcnow(),
				'log_msg': ">>> Connected: {username}, joining {to_channel}",
				'log_params': {
					'username': user['client_nickname'],
					'to_channel': to_chan['channel_name'],
				}
			})
		)
		self.update_client(clid, OrderedDict({'log_parsed_bb': self.parse_log( clid, bbcode=True )}))

		# Output log 
		print( self.parse_log(clid) )

		self._emit('notifycliententerview', data)



	def action_notifytextmessage(self, data):
		parsed = data['event_parsed']
		# {'invokerid': '30223', 'invokername': 'flibbr', 'invokeruid': '/KkbqSqfXWRUhCwlIEXhiHtLv/s=', 'msg': 'test', 'targetmode': '2'}
		pprint(parsed)
		
		# Client id 
		clid = int(parsed['invokerid'])

		# User 
		user = self.get_client(clid)

		# to chanel
		to_chan = self.get_channel(user['cid'])

		# Update client 
		self.update_client( 
			clid, 
			OrderedDict({
				'log_time': datetime.utcnow(),
				'log_msg': "{username} in {to_channel} said: {text_msg}",
				'log_params': {
					'username': user['client_nickname'],
					'to_channel': to_chan['channel_name'],
					'text_msg': parsed['msg'],
				}
			})
		)
		self.update_client(clid, OrderedDict({'log_parsed_bb': self.parse_log( clid, bbcode=True )}))

		# Output log 
		print( self.parse_log(clid) )
		
		data['event_parsed']['txt_msg'] = self.txtmsg_parser.strip(parsed['msg'])

		self._emit('notifytextmessage', data)



	def action_notifyclientmoved(self, data):

		# {'clid': '30223', 'ctid': '44179', 'reasonid': '0'}
		parsed = data['event_parsed']

		# Client id 
		clid = int(parsed['clid'])

		# User 
		user = self.get_client(clid)
		
		# Channel to
		ctid = int(parsed['ctid'])
		to_chan = self.get_channel(ctid)
		self.channel_list[ctid]['total_clients'] += 1 

		# Reason id ..  ? 
		reasonid = int(parsed['reasonid'] )

		log_msg = ''
		log_params = {}
		if 'cid' in user: 
			leave_chan = self.get_channel(user['cid'])
			self.channel_list[user['cid']]['total_clients'] -= 1 

			log_msg = '{username} moved from {from_channel} to {to_channel}'
			log_params = {
				'username': user['client_nickname'],
				'from_channel': leave_chan['channel_name'],
				'to_channel': to_chan['channel_name'],
			}
		else:
			'{username} moved to {to_channel}'
			log_params = {
				'username': user['client_nickname'],
				'to_channel': to_chan['channel_name'],
			}

		# Update client 
		self.update_client( 
			clid, 
			OrderedDict({
				'cid': ctid, # Update that the client to say he's in the new channel
				'log_time': datetime.utcnow(),
				'log_msg': log_msg,
				'log_params': log_params
			})
		)
		self.update_client(clid, OrderedDict({'log_parsed_bb': self.parse_log( clid, bbcode=True )}))

		# Output log 
		print( self.parse_log(clid) )

		self._emit('notifyclientmoved', data)


	def action_notifychanneledited(self, data):

		# {'channel_name': 'Main Room  #eddieknew again', 'cid': '186875', 'invokerid': '30223', 'invokername': 'flibbr', 'invokeruid': '/KkbqSqfXWRUhCwlIEXhiHtLv/s=', 'reasonid': '10' }
		parsed = data['event_parsed']

		# Client id 
		clid = int(parsed['invokerid'])

		# User 
		user = self.get_client(clid)

		# Channel to
		cid = int(parsed['cid'])
		from_chan = self.get_channel(cid)

		# Update client 
		self.update_client( 
			clid, 
			OrderedDict({
				'log_time': datetime.utcnow(),
				'log_msg': "{username} edited {from_channel} name to {to_channel}",
				'log_params': {
					'username': user['client_nickname'],
					'from_channel': from_chan['channel_name'],
					'to_channel': parsed['channel_name'],
				}
			})
		)
		self.update_client(clid, OrderedDict({'log_parsed_bb': self.parse_log( clid, bbcode=True )}))
			
		self.channel_list[cid]['channel_list'] = parsed['channel_name']

		# Output log 
		print( self.parse_log(clid) )

		self._emit('notify_channel_edited', data)


	###############################
	# Channels 
	def build_channels_list(self):
		channel_list = self.ts3conn.channellist()
		channel_list = channel_list.parsed 
		self.channel_list = OrderedDict()
		for c in channel_list: 
			self.channel_list[ int(c['cid']) ] = OrderedDict({
  			'cid': int(c['cid']),
				'channel_name': c['channel_name'],
				'total_clients': int(c['total_clients']),
			})

	def get_channel(self, cid):
		if cid in self.channel_list:
			return self.channel_list[cid]
		else:
			self.build_channels_list()
			return self.channel_list[cid]


	##############################
	# Clients
	def build_clients_list(self):
		client_list = self.ts3conn.clientlist()
		client_list = client_list.parsed 
		
		for c in client_list:
			to_chan = self.get_channel(int(c['cid']))
			clid = int(c['clid'])

			self.client_list[ clid ] = OrderedDict({
				'clid': clid, 
				'cid': int(c['cid']),
				# 'client_country': parsed['client_country'],
				# 'client_created': datetime.utcfromtimestamp(int(parsed['client_created'])), # unixtime
				# 'client_lastconnected': datetime.utcfromtimestamp(int(parsed['client_created'])), # unixtime
				'client_nickname': c['client_nickname'],
				# 'client_nickname_phonetic': parsed['client_nickname_phonetic'],
				# 'client_platform': parsed['client_platform'],
				# 'client_servergroups': OrderedDict({}),
				# 'client_talk_power': int(parsed['client_talk_power']),
				# 'client_totalconnections': int(parsed['client_totalconnections']),
				# 'client_unique_identifier': parsed['client_unique_identifier'],
				# 'server_groups': parsed['client_servergroups'].split(','),
				'log_time': datetime.utcnow(),
				'log_msg': 'saw {username} in {to_channel} when I connected',
				'log_parsed_bb': '',
				'log_params': {
					'username': c['client_nickname'],
					'to_channel': to_chan['channel_name']
				},
			})
			self.update_client(clid, OrderedDict({'log_parsed_bb': self.parse_log( clid, bbcode=True )}),allow_short=True)


	def get_client(self, clid, allow_short=False ):
		logger.debug('Getting client info for {}'.format(clid))

		def build_client(clid):
			parsed = self.ts3conn.clientinfo(clid=clid)._parsed[0]

			self.client_list[clid] = OrderedDict({
				'clid': clid, 
				'cid': int(parsed['cid']),
				'client_country': parsed['client_country'],
				'client_created': datetime.utcfromtimestamp(int(parsed['client_created'])), # unixtime
				'client_lastconnected': datetime.utcfromtimestamp(int(parsed['client_created'])), # unixtime
				'client_nickname': parsed['client_nickname'],
				'client_nickname_phonetic': parsed['client_nickname_phonetic'],
				'client_platform': parsed['client_platform'],
				'client_servergroups': OrderedDict({}),
				'client_talk_power': int(parsed['client_talk_power']),
				'client_totalconnections': int(parsed['client_totalconnections']),
				'client_unique_identifier': parsed['client_unique_identifier'],
				'server_groups': parsed['client_servergroups'].split(','),
				'log_time': datetime.utcnow(),
				'log_msg': '',
				'log_parsed_bb': '',
				'log_params': OrderedDict(),
			})
			self.update_client(clid, OrderedDict({'log_parsed_bb': self.parse_log( clid, bbcode=True )}))

		if clid in self.client_list:
			# Is a 'large' client entry, return it
			if 'client_nickname_phonetic' in self.client_list[clid]:
				logger.debug('Returning full client info for {}'.format(clid))
			# Is a quick client entry, build it.. 
			else:
				if allow_short == False: 
					logger.debug('Building full client info for {}'.format(clid))
					# Get the users existing channel 
					cid = self.client_list[clid]['cid'] 

					# Get the full client info
					build_client(clid)

					# Restore the original channel (incase we're fetching full from a move) 
					self.client_list[clid]['cid'] = cid

		else:
			logger.debug('Making new client for {}'.format(clid))
			build_client(clid)

		
		return self.client_list[clid]


	def update_client(self, clid, data, allow_short=False):

			user = self.get_client(clid, allow_short=allow_short)
			for k,v in data.items():
				self.client_list[clid][k] = v 


	def parse_log(self, clid, bbcode=False):
		end_c = '\033[0m'
		green = '\u001b[38;5;154m'
		green_end = '\033[0m'
		purple = '\u001b[38;5;141m'
		blue = '\u001b[38;5;123m'
		red = '\u001b[38;5;196m' 
		custom = '\u001b[38;5;1'+str(clid)[-2]+str(clid)[-1]+'m'

		if bbcode == True: 
			end_c = '[/color]'
			green = '[color=#336600]'
			purple = '[color=#660066]'
			blue = '[color=#0033cc]'
			red = '[color=#cc0000]' 
			custom = '[color=#003300]'

		user = self.get_client(clid, allow_short=True)

		username = ''
		if 'username' in user['log_params']: 		
			username = "{}({}) {}{}".format(green, user['clid'], user['log_params']['username'], end_c)
			username_short = username 
			username = ("{:<"+str(self.max_name_length)+"}").format(username)

		from_channel = ''
		if 'from_channel' in user['log_params']:
			from_channel = "{}{}{}".format(purple, user['log_params']['from_channel'], end_c)
			from_channel = ("{:<"+str(self.max_cname_length)+"}").format(from_channel)

		to_channel = ''
		if 'to_channel' in user['log_params']:
			to_channel = "{}{}{}".format(blue, user['log_params']['to_channel'] , end_c)
			to_channel = ("{:<"+str(self.max_cname_length)+"}").format(to_channel)


		reasonmsg = '' 
		if 'reasonmsg' in user['log_params']:
			reasonmsg = "{}{}{}".format(red, user['log_params']['reasonmsg'], end_c)

		text_msg = '' 
		if 'text_msg' in user['log_params']:
			text_msg = "{}{}{}".format(custom, user['log_params']['text_msg'], end_c)


		out = user['log_msg'].format(
						username 			= username, 
						# username_short = username_short,
						from_channel  = from_channel,
						to_channel		= to_channel,
						reasonmsg 				=	reasonmsg,
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