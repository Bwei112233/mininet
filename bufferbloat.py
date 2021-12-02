#!/usr/bin/python

"CS144 In-class exercise: Buffer Bloat"

from mininet.topo import Topo
from mininet.node import CPULimitedHost
from mininet.node import Node
from mininet.link import TCLink
from mininet.net import Mininet
from mininet.log import lg, info
from mininet.util import dumpNodeConnections
from mininet.cli import CLI
from monitor import monitor_qlen

from subprocess import Popen, PIPE
from time import sleep, time
from multiprocessing import Process
from argparse import ArgumentParser

import sys
import os

# Parse arguments

parser = ArgumentParser(description="BufferBloat tests")
parser.add_argument('--bw-host', '-B',
                    dest="bw_host",
                    type=float,
                    action="store",
                    help="Bandwidth of host links",
                    required=True)

parser.add_argument('--bw-net', '-b',
                    dest="bw_net",
                    type=float,
                    action="store",
                    help="Bandwidth of network link",
                    required=True)
parser.add_argument('--delay',
                    dest="delay",
                    type=float,
                    help="Delay in milliseconds of host links",
                    default=10)

parser.add_argument('--dir', '-d',
                    dest="dir",
                    action="store",
                    help="Directory to store outputs",
                    default="results",
                    required=True)

parser.add_argument('-n',
                    dest="n",
                    type=int,
                    action="store",
                    help="Number of nodes in star.",
                    required=True)

parser.add_argument('--nflows',
                    dest="nflows",
                    action="store",
                    type=int,
                    help="Number of flows per host (for TCP)",
                    required=True)

parser.add_argument('--maxq',
                    dest="maxq",
                    action="store",
                    help="Max buffer size of network interface in packets",
                    default=500)

parser.add_argument('--cong',
                    dest="cong",
                    help="Congestion control algorithm to use",
                    default="reno")
parser.add_argument('--diff',
                    help="Enabled differential service", 
                    action='store_true',
                    dest="diff",
                    default=False)

parser.add_argument('--exp', '-e',
                    dest="exp",
                    action="store",
                    help="Name of the Experiment",
                    required=True)

# Expt parameters
args = parser.parse_args()

class LinuxRouter( Node ):
    "A Node with IP forwarding enabled."

    # pylint: disable=arguments-differ
    def config( self, **params ):
        super( LinuxRouter, self).config( **params )
        # Enable forwarding on the router
        self.cmd( 'sysctl net.ipv4.ip_forward=1' )

    def terminate( self ):
        self.cmd( 'sysctl net.ipv4.ip_forward=0' )
        super( LinuxRouter, self ).terminate()

class CongTopo(Topo):
    def __init__(self, n=2, cpu=None, bw_host=1000, bw_net=1.5,
                 delay=10, maxq=None, diff=False):
        # Add default members to class.
        super(CongTopo, self ).__init__()
        for i in xrange(3):
            self.addHost( 'h%d' % (i+1), cpu=cpu , ip = '10.0.%d.1/24' % (i+1))
            self.addSwitch( 's%d' % (i+1), fail_mode='open')
            self.addNode( 'r%d' % (i+1), cls=LinuxRouter, ip='10.0.%d.3/24' % (i+1) )

        for i in xrange(3):
            self.addLink('h%d' % (i+1), 's%d' % (i+1), bw=bw_host,
                         max_queue_size=int(maxq))
            self.addLink('s%d' % (i+1), 'r%d' % (i+1), bw=bw_host,
                         max_queue_size=int(maxq))

        self.addLink('s1', 'r2', bw=bw_host, max_queue_size=int(maxq),
                     params2={ 'ip' : '10.0.1.6/24'})

        self.addLink('r1', 'r3', bw=bw_host, max_queue_size=int(maxq),
                     params1={ 'ip' : '10.0.5.1/24'},
                     params2={ 'ip' : '10.0.5.2/24'})

        self.addLink('r2', 'r3', bw=bw_host, max_queue_size=int(maxq),
                     params1={ 'ip' : '10.0.4.3/24'},
                     params2={ 'ip' : '10.0.4.4/24'})


def ping_latency(net):
    "(Incomplete) verify link latency"
    h1 = net.getNodeByName('h1')
    h1.sendCmd('ping -c 10 10.0.3.1')
    result = h1.waitOutput()
    print "Ping result:"
    print result.strip()


def configure_routes(net):
    r1 = net.getNodeByName('r1')
    r2 = net.getNodeByName('r2')
    r3 = net.getNodeByName('r3')
    h1 = net.getNodeByName('h1')
    h2 = net.getNodeByName('h2')
    h3 = net.getNodeByName('h3')

    # my code start
    
    # route pkts intended for h1 at r1 to h1
    r1.cmd("ip route add 10.0.1.0/24 via 10.0.1.3")
    
    # route pkts intended for h1 at r3 to r1
    r3.cmd("ip route add 10.0.1.0/24 via 10.0.5.1")

    # route pkts intended for h3 at r1 to r3
    r1.cmd("ip route add 10.0.3.0/24 via 10.0.5.2")

    # route pkts intended for h2 at h1 to r2
    h1.cmd("ip route add 10.0.2.0/24 via 10.0.1.6")

    # default go through r1, all pkts for h3 from h1 go to r1
    h1.cmd("ip route add default via 10.0.1.3")
    
    r2.cmd("ip route add 10.0.2.0/24 via 10.0.2.3")
    r2.cmd("ip route add 10.0.3.0/24 via 10.0.4.4")
    r2.cmd("ip route add 10.0.1.0/24 via 10.0.1.6")

    r3.cmd("ip route add 10.0.3.0/24 via 10.0.3.3")
    r3.cmd("ip route add default via 10.0.4.3 dev r3-eth2")

    h2.cmd("ip route add default via 10.0.2.3")
    h3.cmd("ip route add default via 10.0.3.3")
    # the end of the part you should edit

    r2.cmd("tc qdisc del dev r2-eth2 root")
    r2.cmd("tc qdisc add dev r2-eth2 root handle 1:0 htb default 1")
    r2.cmd("tc class add dev r2-eth2 parent 1:0 classid 1:1 htb rate 10Mbit ceil 10Mbit")
    r2.cmd('tc qdisc add dev r2-eth2 parent 1:1 handle 10: netem delay 10ms limit %s' % args.maxq)


    r2.cmd('python queue_monitor.py -e %s &'  % args.exp)

def cong_net():
    topo = CongTopo(n=args.n, bw_host=args.bw_host,
                    delay='%sms' % args.delay,
                    bw_net=args.bw_net, maxq=args.maxq, diff=args.diff)
    net = Mininet(topo=topo, host=CPULimitedHost, link=TCLink,
                  autoPinCpus=True)

    net.start()
    configure_routes(net)
    h3 = net.getNodeByName('h3')
    h3.cmd('iperf -s -w 16m -p 5001 -i 1 > output/iperf-recv.txt &')
    ping_latency(net)
    CLI( net )

if __name__ == '__main__':
    cong_net()
