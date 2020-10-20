"""Module containing the connection wrapper for the AVR interface."""
import asyncio
import logging
from pyeiscp.protocol import AVR

__all__ = "Connection"

try:
    ensure_future = asyncio.ensure_future
except:
    ensure_future = getattr(asyncio, "async")


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
        connect_callback=None
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
        :param update_callback"
            This function is called whenever AVR state data changes

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

        await conn._reconnect()

        return conn

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
