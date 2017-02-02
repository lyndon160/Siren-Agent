import json
import socket
import requests
import logging
import urllib2
import time
import netifaces
import os
from IPy import IP

import shlex
from subprocess import Popen, PIPE
from uptime import uptime
import pyping
from subprocess import call


'''Updates discovery database of presence of a Fog compute node'''


class Agent:
    def __init__(self):
        # Read server ip


        logging.basicConfig(level=logging.DEBUG)


        logging.debug("Starting agent")

        with open('config.json') as json_data:
            self.data = json.load(json_data)
            self.server_ip = self.data['server_ip']
            self.poll_time = self.data['poll_time']
            self.anchors = self.data['anchor_nodes']

        # Get node ip. This might not get the right IP address but will be changed if public ip is given
        # Do not use gethostname as container will have different hostname
        self.node_ip = None#socket.gethostbyname(socket.gethostname())
        #    ip_type = IP(node_ip).iptype()

        self.interface_dict = {}

        self.agent_daemon()

    '''Repeats tests and reports to discovery server'''
    def agent_daemon(self):
        # After time (defined in config.json) update information in case node has new ip
        logging.debug("Daemon started")
        while (True):
            # Build request
            #Data should be Net, sys, and stats
            self.update_net()
            self.update_sys()
            self.gather_net_stats()
            self.report_stats()
            logging.debug("Stats reported. Waiting " + str(self.poll_time) + " seconds")
            time.sleep(self.poll_time)

    '''Get network information from system'''
    def update_net(self):
        logging.debug("Updating network info")
        # Look through all interfaces and get ip addresses
        for interface in netifaces.interfaces():
            if (not "lo" in interface):
                addr_info = netifaces.ifaddresses(interface)
                for k in addr_info:
                    for i in addr_info[k]:
                        if (not len(i['addr']) == 17):
                            tmp_ip = i['addr']
                            logging.debug('ip addr :' + tmp_ip)
                            #if (len(tmp_ip) == 31):
                                # Cut metadata from IPv6 addr
                            #    tmp_ip = tmp_ip.split('%')[0]
                            try:
                                IP(tmp_ip)
                                self.interface_dict[interface] = tmp_ip
                            except:
                                logging.warning("Ignoring invalid IP %s", tmp_ip)

        for interface in self.interface_dict:
            if(IP(self.interface_dict[interface]).iptype() == "PUBLIC"):
                logging.debug("Public address found :" + self.interface_dict[interface])
                self.node_ip = self.interface_dict[interface]
            else:
                logging.debug("Discarding private ip " + self.interface_dict[interface])
        if self.node_ip == None and len(self.interface_dict)>0:
            self.node_ip = self.interface_dict.values()[0]


    '''Get information about the system'''
    def update_sys(self):
        logging.debug("Getting system info")
        self.load = os.getloadavg()
        self.up_time = uptime()
        self.sys_stats = {'load':self.load, 'uptime':self.up_time}
        return self.sys_stats

    '''Perform network tests against anchor nodes'''
    def gather_net_stats(self):
        #Do ping to all anchor nodes
        logging.debug("Gathering network stats")
        self.anchor_stats = {}
        for anchor_ip in self.anchors:
            logging.debug("Trying ping to " + anchor_ip)
            try:
                ping_response = pyping.ping(anchor_ip)
                if(ping_response.ret_code == 0):
                    latency = ping_response.avg_rtt
                else:
                    latency = -1
            except:
                latency = -1
                logging.warning("Not root. Cannot perform ping.")

            logging.debug("Latency to anchor " + anchor_ip + " :" +  str(latency))
            #Do Iperf here.
            throughput = -1

            #Check to see if not osx (does not work with osx)
            #if(not "Darwin" in os.uname()):
            logging.debug("Trying iperf to " + anchor_ip)
            try:

                logging.debug("Doing iperf now")
                #result = json.loads(call("iperf3 -c " +  anchor_ip  + " -J", shell=True))

                exitcode, out, err = self.get_exitcode_stdout_stderr("iperf3 -c " +  anchor_ip  + " -J")
                iperf_result = json.loads(out)
                throughput = int(iperf_result['end']['sum_received']['bits_per_second'])/1024/1024
                logging.debug("Throughput to anchor :" + anchor_ip + " at " + str(throughput) + "mbps")
            except Exception as e:
                logging.warning("Iperf test to anchor %s failed because %s", anchor_ip, e)
            #else:
                #logging.warning("OSX not supported")

            #Do tracert here
            hops = 10

            self.anchor_stats[anchor_ip] = {"latency":latency, "throughput":throughput, "hops": hops}
            return self.anchor_stats

    '''Send all stats to discovery server'''
    def report_stats(self):
        logging.debug("Sending stats to %s", self.server_ip)
        data = {"ip": self.node_ip, "anchor_stats": self.anchor_stats, "system_stats": self.sys_stats}
        req = urllib2.Request('http://' + self.server_ip + ':61112/nodes/register_node')
        req.add_header('Content-Type', 'application/json')
        retry = True
        while (retry):
            try:
                response = urllib2.urlopen(req, json.dumps(data))
            except urllib2.HTTPError as e:
                retry = True
                time.sleep(30)
                logging.warning("Could not make connection to discovery server")
            else:
                retry = False

    def get_exitcode_stdout_stderr(self,  cmd):
        """
        Execute the external command and get its exitcode, stdout and stderr.
        """
        args = shlex.split(cmd)

        proc = Popen(args, stdout=PIPE, stderr=PIPE)
        out, err = proc.communicate()
        exitcode = proc.returncode
        #
        return exitcode, out, err

if __name__ == '__main__':
    Agent()