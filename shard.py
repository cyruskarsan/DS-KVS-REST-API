from uhashring import HashRing

# create a consistent hash ring of 3 nodes of weight 1
hr = HashRing(nodes=['node1', 'node2', 'node3'])

# get the node name for the 'coconut' key
target_node = hr.get_node('coconut')
print(target_node)


#return all shard id's as a list
def getShards():
	return list(hr.get_nodes())

#given the socket address of a replica, return the replica in the shard
def getNodeID():
	return None

#returns the number of keys in a shard
def numKeys()