"""Module containing the connection wrapper for the AVR interface."""
import asyncio
import logging
import socket
import netifaces
from pyeiscp.protocol import AVR, eISCPPacket

__all__ = "Connection"

try:
    ensure_future = asyncio.ensure_future
except:
    ensure_future = getattr(asyncio, "async")


class DiscoveryProtocol(asyncio.DatagramProtocol):

    def __init__(self,
        target,
        discovered_callback=None,
        loop=None,
    ):
        """Protocol handler that handles AVR discovery by broadcasting a discovery packet.

            :param target:
                the target (host, port) to broadcast the discovery packet over
            :param discovered_callback:
                called when a device has been discovered (optional)
            :param loop:
                asyncio event loop (optional)

            :type target:
                tuple
            :type: discovered_callback:
                coroutine
            :type loop:
                asyncio.loop
        """
        self.log = logging.getLogger(__name__)
        self._target = target
        self._discovered_callback = discovered_callback
        self._loop = loop

        self.discovered = []
        self.transport = None

    def connection_made(self, transport):
        """Discovery connection created, broadcast discovery packet."""
        self.transport = transport
        self.broadcast_discovery_packet()

    def datagram_received(self, data, addr):
        """Received response from device."""
        info = eISCPPacket.parse_info(data)
        if info and info['identifier'] not in self.discovered:
            self.log.info(f"{info['model_name']} discovered at {addr}")
            self.discovered.append(info['identifier'])
            if self._discovered_callback:
                ensure_future(self._discovered_callback(addr[0], int(info['iscp_port']), info['model_name'], info['identifier']))

    def broadcast_discovery_packet(self):
        """Broadcast discovery packets over the target."""
        self.log.debug(f"Broadcast discovery packet to {self._target}")
        self.transport.sendto(eISCPPacket('!xECNQSTN').get_raw(), self._target)
        self.transport.sendto(eISCPPacket('!pECNQSTN').get_raw(), self._target)

    def close(self):
        """Close the discovery connection."""
        self.log.debug("Closing broadcast discovery connection")
        if self.transport:
            self.transport.close()

    async def async_close_delayed(self, delay):
        """Close the discovery connection after a certain delay."""
        await asyncio.sleep(delay)
        self.close()


class Connection:
    """Connection handler to maintain network connection for AVR Protocol."""

    def __init__(self):
        """Instantiate the Connection object."""
        self.log = logging.getLogger(__name__)

    @classmethod
    async def create(
        cls,
        host="localhost",
        port=60128,
        auto_reconnect=True,
        loop=None,
        protocol_class=AVR,
        update_callback=None,
        connect_callback=None,
        auto_connect=True
    ):
        """Initiate a connection to a specific device.

        Here is where we supply the host and port and callback callables we
        expect for this AVR class object.

        :param host:
            Hostname or IP address of the device
        :param port:
            TCP port number of the device
        :param auto_reconnect:
            Should the Connection try to automatically reconnect if needed?
        :param loop:
            asyncio.loop for async operation
        :param update_callback
            This function is called whenever AVR state data changes
        :param connect_callback
            This function is called when the connection with the AVR is established
        :param auto_connect
            Should the Connection try to automatically connect?

        :type host:
            str
        :type port:
            int
        :type auto_reconnect:
            boolean
        :type loop:
            asyncio.loop
        :type update_callback:
            callable
        :type connect_callback:
            callable
        :type auto_connect:
            boolean
        """
        assert port >= 0, "Invalid port value: %r" % (port)
        conn = cls()

        conn.host = host
        conn.port = port
        conn._loop = loop or asyncio.get_event_loop()
        conn._retry_interval = 1
        conn._closed = False
        conn._closing = False
        conn._halted = False
        conn._auto_reconnect = auto_reconnect

        def connection_lost():
            """Function callback for Protocoal class when connection is lost."""
            if conn._auto_reconnect and not conn._closing:
                ensure_future(conn._reconnect(), loop=conn._loop)

        conn.protocol = protocol_class(
            connection_lost_callback=connection_lost,
            loop=conn._loop,
            update_callback=update_callback,
            connect_callback=connect_callback,
        )

        if auto_connect:
            await conn._reconnect()

        return conn

    @classmethod
    async def discover(
        cls,
        port=60128,
        auto_reconnect=True,
        loop=None,
        protocol_class=AVR,
        update_callback=None,
        connect_callback=None,
        discovery_callback=None,
        timeout = 5
    ):
        """Discover Onkyo or Pioneer AVRs on the network.

        Here we discover devices on the available networks and for every
        discovered device, a Connection object is returned through the
        discovery callback coroutine. The connection is not yet established,
        this should be done manually by calling connect on the Connection

        :param port:
            TCP port number of the device
        :param auto_reconnect:
            Should the Connection try to automatically reconnect if needed?
        :param loop:
            asyncio.loop for async operation
        :param update_callback
            This function is called whenever discovered devices state data change
        :param connect_callback
            This function is called when the connection with discovered devices is established
        :param discovery_callback
            This function is called when a device has been discovered on the network
        :param timeout
            Number of seconds to detect devices

        :type port:
            int
        :type auto_reconnect:
            boolean
        :type loop:
            asyncio.loop
        :type update_callback:
            callable
        :type connect_callback:
            callable
        :type discovery_callback:
            coroutine
        :type timeout
            int
        """
        assert port >= 0, "Invalid port value: %r" % (port)

        _loop = loop or asyncio.get_event_loop()

        async def discovered_callback(host, port, name, identifier):
            """Async function callback for Discovery Protocol when an AVR is discovered"""

            def _update_callback(message):
                """Function callback for Protocol class when the AVR sends updates."""
                if update_callback:
                    _loop.call_soon(update_callback, message, host)

            # Create a Connection, but do not auto connect
            conn = await cls.create(
                host=host,
                port=port,
                auto_reconnect=auto_reconnect,
                loop=_loop,
                protocol_class=protocol_class,
                update_callback=_update_callback,
                connect_callback=connect_callback,
                auto_connect=False
            )

            # Pass the created Connection to the discovery callback
            conn.name = name
            conn.identifier = identifier
            if discovery_callback:
                ensure_future(discovery_callback(conn))

        # Iterate over all network interfaces to find broadcast addresses
        for interface in netifaces.interfaces():
            ifaddrs=netifaces.ifaddresses(interface)
            if not netifaces.AF_INET in ifaddrs:
                continue
            for ifaddr in ifaddrs[netifaces.AF_INET]:
                if not "addr" in ifaddr or not "broadcast" in ifaddr:
                    continue
                try:
                    # Create a DiscoveryProtocol for each broadcast address to broadcast the discovery packets.
                    protocol = DiscoveryProtocol(target=(ifaddr["broadcast"], port), discovered_callback=discovered_callback, loop=_loop)
                    await _loop.create_datagram_endpoint( lambda: protocol, local_addr=(ifaddr["addr"], 0), allow_broadcast=True )
                    # Close the DiscoveryProtocol connections after timeout seconds
                    ensure_future(protocol.async_close_delayed(timeout))
                except PermissionError:
                    continue

    def update_property(self, zone, propname, value):
        """Format an update message and send to the receiver."""
        self.send(f"{zone}.{propname}={value}")

    def query_property(self, zone, propname):
        """Format a query message and send to the receiver."""
        self.send(f"{zone}.{propname}=query")

    def send(self, msg):
        """Fire and forget data to the reciever."""
        self.protocol.command(msg)

    def _get_retry_interval(self):
        return self._retry_interval

    def _reset_retry_interval(self):
        self._retry_interval = 1

    def _increase_retry_interval(self):
        self._retry_interval = min(300, 1.5 * self._retry_interval)

    async def _reconnect(self):
        while True:
            try:
                if self._halted:
                    await asyncio.sleep(2, loop=self._loop)
                else:
                    self.log.info(
                        "Connecting to Onkyo AVR at %s:%d", self.host, self.port
                    )
                    await self._loop.create_connection(
                        lambda: self.protocol, self.host, self.port
                    )
                    self._reset_retry_interval()
                    return

            except OSError:
                self._increase_retry_interval()
                interval = self._get_retry_interval()
                self.log.warning("Connecting failed, retrying in %i seconds", interval)
                await asyncio.sleep(interval, loop=self._loop)

    async def connect(self):
        """Establish the AVR device connection"""
        if not self.protocol.transport:
            await self._reconnect()

    def close(self):
        """Close the AVR device connection and don't try to reconnect."""
        self.log.info("Closing connection to AVR")
        self._closing = True
        if self.protocol.transport:
            self.protocol.transport.close()

    def halt(self):
        """Close the AVR device connection and wait for a resume() request."""
        self.log.info("Halting connection to AVR")
        self._halted = True
        if self.protocol.transport:
            self.protocol.transport.close()

    def resume(self):
        """Resume the AVR device connection if we have been halted."""
        self.log.info("Resuming connection to AVR")
        self._halted = False

    @property
    def dump_conndata(self):
        """Developer tool for debugging forensics."""
        attrs = vars(self)
        return ", ".join("%s: %s" % item for item in attrs.items())
