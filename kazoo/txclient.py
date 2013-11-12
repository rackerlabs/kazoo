"""
Twisted binding for kazoo library

This isn't really Twistified Kazoo. Instead it delegates all blocking calls to seperate
thread and returns result as Deferred. This allows usage of using Kazoo in Twisted reactor
thread without blocking it
"""

from twisted.internet import reactor
from twisted.internet.threads import deferToThread

from kazoo.client import KazooClient
from kazoo.recipe.partitioner import PartitionState


class TxKazooClient(object):
    """
    Twisted wrapper for `kazoo.client.KazooClient`

    Implements blocking methods of `kazoo.client.KazooClient` in seperate thread
    and return Deferred that fires with method's result or errbacks with any exception
    occurred during method execution
    """

    kz_get_attributes = ['handler', 'retry', 'state', 'client_state', 'client_id', 'connected']

    def __init__(self, **kwargs):
        """
        Initialize `TxKazooClient`

        Takes same arguments as KazooClient and extra keyword argument `threads`
        that suggests thread pool size to be used
        """
        threads = kwargs.pop('threads', 10)
        reactor.suggestThreadPoolSize(threads)
        self.client = KazooClient(**kwargs)
        self._internal_listeners = dict()

    def __getattr__(self, name):
        """
        Delegates method executions to a thread pool. Does this by returning
        a function that calls `KazooClient.method` in seperate thread if `name` is
        method name

        :return: `Deferred` that fires with result of executed method if `name`
                 is name of method. Otherwise, `KazooClient.property` value is returned
        """
        if name in self.kz_get_attributes:
            # Assuming all attributes access are not blocking
            return getattr(self.client, name)
        return lambda *args, **kwargs: deferToThread(getattr(self.client, name), *args, **kwargs)

    def add_listener(self, listener):
        # This call does not block and is probably not thread safe. It is best if it
        # is called from twisted reactor thread only

        def _listener(state):
            # Called from kazoo thread. Replaying the original listener in reactor
            # thread
            reactor.callFromThread(listener, state)

        self._internal_listeners[listener] = _listener
        return self.client.add_listener(_listener)

    def remove_listener(self, listener):
        _listener = self._internal_listeners.pop(listener)
        self.client.remove_listener(_listener)

    def _watch_func(self, func, path, watch=None, **kwargs):
        if not watch:
            return deferToThread(func, path, **kwargs)

        def _watch(event):
            # Called from kazoo thread. Replaying in reactor
            reactor.callFromThread(watch, event)

        return deferToThread(func, path, watch=_watch, **kwargs)

    def exists(self, path, watch=None):
        return self._watch_func(self.client.exists, path, watch)

    def exists_async(self, path, watch=None):
        return self._watch_func(self.client.exists_async, path, watch)

    def get(self, path, watch=None):
        return self._watch_func(self.client.get, path, watch)

    def get_async(self, path, watch=None):
        return self._watch_func(self.client.get_async, path, watch)

    def get_children(self, path, watch=None, include_data=False):
        return self._watch_func(self.client.get_children, path, watch, include_data=include_data)

    def get_children_async(self, path, watch=None, include_data=False):
        return self._watch_func(self.client.get_children_async, path, watch, include_data=include_data)

    def Lock(self, path, identifier=None):
        """
        Return Twisted wrapper for `Lock` object corresponding to this client.
        """
        return Lock(self.client.Lock(path, identifier))

    def SetPartitioner(self, path, set, **kwargs):
        """
        Return Twisted wrapper for `SetPartitioner` object corresponding to this client.
        """
        return SetPartitioner(self.client, path, set, **kwargs)


class Lock(object):
    """
    Twisted wrapper for `kazoo.recipe.lock.Lock` object

    All the methods in `kazoo.recipe.lock.Lock` are delegated to thread
    """

    def __init__(self, lock):
        """
        Initialize Lock

        :param lock: `kazoo.recipe.lock.Lock` object
        """
        self._lock = lock

    def __getattr__(self, name):
        return lambda *args, **kwargs: deferToThread(getattr(self._lock, name), *args, **kwargs)


class SetPartitioner(object):
    """
    Twisted wrapper for SetPartitioner
    """

    # These attributes do not block and hence will be given directly
    get_attrs = ['failed', 'release', 'acquired']

    def __init__(self, client, path, set, **kwargs):
        """
        Initializes SetPartitioner. Takes same arguments as
        `kazoo.recipe.partitioner.SetPartitioner.__init__`
        """
        self._partitioner = None
        d = deferToThread(client.SetPartitioner, path, set, **kwargs)
        d.addCallback(self._initialized)

    def _initialized(self, partitioner):
        self._partitioner = partitioner

    @property
    def state(self):
        # Until paritioner is initialzed, we know that it is allocating
        return PartitionState.ALLOCATING if not self._partitioner else self._partitioner.state

    @property
    def allocating(self):
        return self.state == PartitionState.ALLOCATING

    def __getattr__(self, name):
        if name in self.get_attrs:
            # Until paritioner is initialzed, we know state is allocating and hence other
            # properties will be False
            return False if not self._partitioner else getattr(self._partitioner, name)
        return lambda *args, **kwargs: deferToThread(getattr(self._partitioner, name), *args, **kwargs)

    def __iter__(self):
        for elem in self._partitioner:
            yield elem

