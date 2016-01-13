# Copyright (C) 2015 Savoir-Faire Linux Inc.
# Author(s): Adrien Béraud <adrien.beraud@savoirfairelinux.com>
#            Simon Désaulniers <sim.desaulniers@gmail.com>

import threading
import random
import string
import time

from opendht import *
from dht.network import DhtNetwork, DhtNetworkSubProcess


def random_hash():
    return InfoHash(''.join(random.SystemRandom().choice(string.hexdigits) for _ in range(40)).encode())


class FeatureTest(object):
    """
    This is base test. A method run() implementation is required.
    """
    #static variables used by class callbacks
    successfullTransfer = lambda lv,fv: len(lv) == len(fv)
    done = 0
    lock = None
    foreign_nodes = None
    foreign_values = None

    def __init__(self, test, workbench):
        self._test = test
        self.wb = workbench
        self.bootstrap = self.wb.get_bootstrap()

    @staticmethod
    def getcb(value):
        DhtNetwork.log('[GET]: %s' % value)
        FeatureTest.foreign_values.append(value)
        return True

    @staticmethod
    def putDoneCb(ok, nodes):
        if not ok:
            DhtNetwork.log("[PUT]: failed!")
        with FeatureTest.lock:
            FeatureTest.done -= 1
            FeatureTest.lock.notify()

    @staticmethod
    def getDoneCb(ok, nodes):
        with FeatureTest.lock:
            if not ok:
                DhtNetwork.log("[GET]: failed!")
            else:
                for node in nodes:
                    if not node.getNode().isExpired():
                        FeatureTest.foreign_nodes.append(node.getId().toString())
            FeatureTest.done -= 1
            FeatureTest.lock.notify()

    def _dhtPut(self, producer, _hash, *values):
        for val in values:
            with FeatureTest.lock:
                DhtNetwork.log('[PUT]: %s' % val)
                FeatureTest.done += 1
                producer.put(_hash, val, FeatureTest.putDoneCb)
                while FeatureTest.done > 0:
                    FeatureTest.lock.wait()

    def _dhtGet(self, consumer, _hash):
        FeatureTest.foreign_values = []
        FeatureTest.foreign_nodes = []
        with FeatureTest.lock:
            FeatureTest.done += 1
            consumer.get(_hash, FeatureTest.getcb, FeatureTest.getDoneCb)
            while FeatureTest.done > 0:
                FeatureTest.lock.wait()


    def run(self):
        raise NotImplementedError('This method must be implemented.')


class PersistenceTest(FeatureTest):
    """
    This tests persistence of data on the network.
    """

    def __init__(self, test, workbench, *opts):
        """
        @param test: is one of the following:
                     - 'mult_time': test persistence of data based on internal
                       OpenDHT storage maintenance timings.
                     - 'delete': test persistence of data upon deletion of
                       nodes.
                     - 'replace': replacing cluster successively.
        @type  test: string


        OPTIONS

        - dump_str_log: enables storage log at test ending.
        """

        # opts
        super(PersistenceTest, self).__init__(test, workbench)
        self._dump_storage = True if 'dump_str_log' in opts else False
        self._plot = True if 'plot' in opts else False

    def _result(self, local_values, new_nodes):
        bootstrap = self.bootstrap
        if not FeatureTest.successfullTransfer(local_values, FeatureTest.foreign_values):
            DhtNetwork.log('[GET]: Only %s on %s values persisted.' %
                    (len(FeatureTest.foreign_values), len(local_values)))
        else:
            DhtNetwork.log('[GET]: All values successfully persisted.')
        if FeatureTest.foreign_values:
            if new_nodes:
                DhtNetwork.log('Values are newly found on:')
                for node in new_nodes:
                    DhtNetwork.log(node)
                if self._dump_storage:
                    DhtNetwork.log('Dumping all storage log from '\
                                  'hosting nodes.')

                    for proc in self.wb.procs:
                        proc.sendDumpStorage(FeatureTest.foreign_nodes)
            else:
                DhtNetwork.log("Values didn't reach new hosting nodes after shutdown.")

    def run(self):
        try:
            if self._test == 'delete':
                self._deleteTest()
            elif self._test == 'replace':
                self._replaceClusterTest()
            elif self._test == 'mult_time':
                self._multTimeTest()
        except Exception as e:
            print(e)
        finally:
            self.bootstrap.resize(1)

    #-----------
    #-  Tests  -
    #-----------

    def _deleteTest(self):
        """
        It uses Dht shutdown call from the API to gracefuly finish the nodes one
        after the other.
        """
        FeatureTest.done = 0
        FeatureTest.lock = threading.Condition()
        FeatureTest.foreign_nodes = []
        FeatureTest.foreign_values = []

        bootstrap = self.bootstrap

        ops_count = []

        bootstrap.resize(3)
        consumer = bootstrap.get(1)
        producer = bootstrap.get(2)

        myhash = random_hash()
        local_values = [Value(b'foo'), Value(b'bar'), Value(b'foobar')]

        self._dhtPut(producer, myhash, *local_values)

        #checking if values were transfered
        self._dhtGet(consumer, myhash)
        if not FeatureTest.successfullTransfer(local_values, FeatureTest.foreign_values):
            if FeatureTest.foreign_values:
                DhtNetwork.log('[GET]: Only ', len(FeatureTest.foreign_values) ,' on ',
                        len(local_values), ' values successfully put.')
            else:
                DhtNetwork.log('[GET]: 0 values successfully put')


        if FeatureTest.foreign_values and FeatureTest.foreign_nodes:
            DhtNetwork.log('Values are found on :')
            for node in FeatureTest.foreign_nodes:
                DhtNetwork.log(node)

            #DhtNetwork.log("Waiting a minute for the network to settle down.")
            #time.sleep(60)

            for _ in range(max(1, int(self.wb.node_num/32))):
                DhtNetwork.log('Removing all nodes hosting target values...')
                cluster_ops_count = 0
                for proc in self.wb.procs:
                    DhtNetwork.log('[REMOVE]: sending shutdown request to', proc)
                    proc.sendNodesRequest(
                            DhtNetworkSubProcess.SHUTDOWN_NODE_REQ,
                            FeatureTest.foreign_nodes
                    )
                    DhtNetwork.log('sending message stats request')
                    stats = proc.sendGetMessageStats()
                    cluster_ops_count += sum(stats[1:])
                    DhtNetwork.log("3 seconds wait...")
                    time.sleep(3)
                ops_count.append(cluster_ops_count/self.wb.node_num)

                # checking if values were transfered to new nodes
                foreign_nodes_before_delete = FeatureTest.foreign_nodes
                DhtNetwork.log('[GET]: trying to fetch persistent values')
                self._dhtGet(consumer, myhash)
                new_nodes = set(FeatureTest.foreign_nodes) - set(foreign_nodes_before_delete)

                self._result(local_values, new_nodes)

            if self._plot:
                plt.plot(ops_count, color='blue')
                plt.draw()
                plt.ioff()
                plt.show()
        else:
            DhtNetwork.log("[GET]: either couldn't fetch values or nodes hosting values...")

    #TODO: complete this test.
    def _replaceClusterTest(self):
        """
        It replaces all clusters one after the other.
        """
        FeatureTest.done = 0
        FeatureTest.lock = threading.Condition()
        FeatureTest.foreign_nodes = []
        FeatureTest.foreign_values = []

        #clusters = opts['clusters'] if 'clusters' in opts else 5
        clusters = 5

        bootstrap = self.bootstrap

        bootstrap.resize(3)
        consumer = bootstrap.get(1)
        producer = bootstrap.get(2)

        myhash = random_hash()
        local_values = [Value(b'foo'), Value(b'bar'), Value(b'foobar')]

        self._dhtPut(producer, myhash, *local_values)
        self._dhtGet(consumer, myhash)
        initial_nodes = FeatureTest.foreign_nodes

        DhtNetwork.log('Replacing', clusters, 'random clusters successively...')
        for n in range(clusters):
            i = random.randint(0, len(self.wb.procs)-1)
            proc = self.wb.procs[i]
            DhtNetwork.log('Replacing', proc)
            proc.sendShutdown()
            self.wb.stop_cluster(i)
            self.wb.start_cluster(i)

        DhtNetwork.log('[GET]: trying to fetch persistent values')
        self._dhtGet(consumer, myhash)
        new_nodes = set(FeatureTest.foreign_nodes) - set(initial_nodes)

        self._result(local_values, new_nodes)

    #TODO: complete this test.
    def _multTimeTest(self):
        """
        Multiple put() calls are made from multiple nodes to multiple hashes
        after what a set of 8 nodes is created around each hashes in order to
        enable storage maintenance each nodes. Therefor, this tests will wait 10
        minutes for the nodes to trigger storage maintenance.
        """
        FeatureTest.done = 0
        FeatureTest.lock = threading.Condition()
        FeatureTest.foreign_nodes = []
        FeatureTest.foreign_values = []
        bootstrap = self.bootstrap

        N_PRODUCERS = 16

        hashes = []
        values = [Value(b'foo')]
        nodes = set([])

        # prevents garbage collecting of unused flood nodes during the test.
        flood_nodes = []

        def gottaGetThemAllPokeNodes(nodes=None):
            nonlocal consumer, hashes
            for h in hashes:
                self._dhtGet(consumer, h)
                if nodes is not None:
                    for n in FeatureTest.foreign_nodes:
                        nodes.add(n)

        def createNodesAroundHash(_hash, radius=4):
            nonlocal flood_nodes

            _hash_str = _hash.toString().decode()
            _hash_int = int(_hash_str, 16)
            for i in range(-radius, radius+1):
                _hash_str = '{:40x}'.format(_hash_int + i)
                config = DhtConfig()
                config.setNodeId(InfoHash(_hash_str.encode()))
                n = DhtRunner()
                n.run(config=config)
                n.bootstrap(self.bootstrap.ip4,
                            str(self.bootstrap.port))
                flood_nodes.append(n)

        bootstrap.resize(N_PRODUCERS+2)
        consumer = bootstrap.get(1)
        producers = (bootstrap.get(n) for n in range(2,N_PRODUCERS+2))
        for p in producers:
            hashes.append(random_hash())
            self._dhtPut(p, hashes[-1], *values)

        gottaGetThemAllPokeNodes(nodes=nodes)

        DhtNetwork.log("Values are found on:")
        for n in nodes:
            DhtNetwork.log(n)

        DhtNetwork.log("Creating 8 nodes around all of these nodes...")
        for _hash in hashes:
            createNodesAroundHash(_hash)

        DhtNetwork.log('Waiting 10 minutes for normal storage maintenance.')
        time.sleep(10*60)

        DhtNetwork.log('Deleting old nodes from previous search.')
        for proc in self.wb.procs:
            DhtNetwork.log('[REMOVE]: sending shutdown request to', proc)
            proc.sendNodesRequest(
                DhtNetworkSubProcess.REMOVE_NODE_REQ,
                nodes
            )

        # new consumer (fresh cache)
        bootstrap.resize(N_PRODUCERS+3)
        consumer = bootstrap.get(N_PRODUCERS+2)

        nodes_after_time = set([])
        gottaGetThemAllPokeNodes(nodes=nodes_after_time)
        self._result(values, nodes_after_time - nodes)


class PerformanceTest(FeatureTest):
    """
    Tests for general performance of dht operations.
    """

    def __init__(self, test, workbench, *opts):
        """
        @param test: is one of the following:
                     - 'gets': multiple get operations and statistical results.
                     - 'delete': perform multiple put() operations followed
                       by targeted deletion of nodes hosting the values. Doing
                       so until half of the nodes on the network remain.
        @type  test: string
        """
        super(PerformanceTest, self).__init__(test, workbench)

    def run(self):
        try:
            if self._test == 'gets':
                self._getsTimesTest()
            elif self._test == 'delete':
                self._delete()
        except Exception as e:
            print(e)
        finally:
            self.bootstrap.resize(1)


    ###########
    #  Tests  #
    ###########

    def _getsTimesTest(self):
        """
        Tests for performance of the DHT doing multiple get() operation.
        """
        bootstrap = self.bootstrap

        plt.ion()

        fig, axes = plt.subplots(2, 1)
        fig.tight_layout()

        lax = axes[0]
        hax = axes[1]

        lines = None#ax.plot([])
        #plt.ylabel('time (s)')
        hax.set_ylim(0, 2)

        # let the network stabilise
        plt.pause(60)

        #start = time.time()
        times = []

        lock = threading.Condition()
        done = 0

        def getcb(v):
            nonlocal bootstrap
            DhtNetwork.log("found", v)
            return True

        def donecb(ok, nodes):
            nonlocal bootstrap, lock, done, times
            t = time.time()-start
            with lock:
                if not ok:
                    DhtNetwork.log("failed !")
                times.append(t)
                done -= 1
                lock.notify()

        def update_plot():
            nonlocal lines
            while lines:
                l = lines.pop()
                l.remove()
                del l
            lines = plt.plot(times, color='blue')
            plt.draw()

        def run_get():
            nonlocal done
            done += 1
            start = time.time()
            bootstrap.front().get(InfoHash.getRandom(), getcb, lambda ok, nodes: donecb(ok, nodes, start))

        plt.pause(5)

        plt.show()
        update_plot()

        times = []
        for n in range(10):
            self.wb.replace_cluster()
            plt.pause(2)
            DhtNetwork.log("Getting 50 random hashes succesively.")
            for i in range(50):
                with lock:
                    done += 1
                    start = time.time()
                    bootstrap.front().get(InfoHash.getRandom(), getcb, donecb)
                    while done > 0:
                        lock.wait()
                        update_plot()
                update_plot()
            print("Took", np.sum(times), "mean", np.mean(times), "std", np.std(times), "min", np.min(times), "max", np.max(times))

        print('GET calls timings benchmark test : DONE. '  \
                'Close Matplotlib window for terminating the program.')
        plt.ioff()
        plt.show()

    def _delete(self):
        """
        Tests for performance of get() and put() operations on the network while
        deleting around the target hash.
        """

        FeatureTest.done = 0
        FeatureTest.lock = threading.Condition()
        FeatureTest.foreign_nodes = []
        FeatureTest.foreign_values = []

        bootstrap = self.bootstrap

        bootstrap.resize(3)
        consumer = bootstrap.get(1)
        producer = bootstrap.get(2)

        myhash = random_hash()
        local_values = [Value(b'foo'), Value(b'bar'), Value(b'foobar')]

        for _ in range(max(1, int(self.wb.node_num/32))):
            self._dhtGet(consumer, myhash)
            DhtNetwork.log("Waiting 15 seconds...")
            time.sleep(15)

            self._dhtPut(producer, myhash, *local_values)

            #checking if values were transfered
            self._dhtGet(consumer, myhash)
            DhtNetwork.log('Values are found on :')
            for node in FeatureTest.foreign_nodes:
                DhtNetwork.log(node)

            if not FeatureTest.successfullTransfer(local_values, FeatureTest.foreign_values):
                if FeatureTest.foreign_values:
                    DhtNetwork.log('[GET]: Only ', len(FeatureTest.foreign_values) ,' on ',
                            len(local_values), ' values successfully put.')
                else:
                    DhtNetwork.log('[GET]: 0 values successfully put')

            DhtNetwork.log('Removing all nodes hosting target values...')
            for proc in self.wb.procs:
                DhtNetwork.log('[REMOVE]: sending shutdown request to', proc)
                proc.sendNodesRequest(
                        DhtNetworkSubProcess.SHUTDOWN_NODE_REQ,
                        FeatureTest.foreign_nodes
                )