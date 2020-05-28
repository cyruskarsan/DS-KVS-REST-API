# assignment3.py
#
# Contains the implementation of a replicated fault-tolerant
# key-value store that provides casual consistency.

from flask import Flask, jsonify, request, abort
from flask_restful import Api, Resource, reqparse
from requests.exceptions import Timeout
from uhashring import HashRing
import json
import os
import requests as req
import time


app = Flask(__name__)
api = Api(app)

# Tweak variable, timeout until a replica is considered failed. 
# Only for accessing other replicas!
timeoutduration = 15

# Global variable, dictionary of keys and values. 
dic = {}
# Global variable, array of ip:port combinations of all other operational replicas.
viewstore = None
# Global variable, current ip:port combination.
socketaddr = ""
# Global variable for causal-metadata.
causalmetadata = ""

vector_clock = []
total_differences = 0
replicaNumber = None
NumberOfReplicas = None

#global var containing all known shards
shardcnt=""

# Used to delete an address from viewstore and broadcast that 
# address to the rest of the replicas. This function is triggered by 
# try:except statements when trying to access all of the replicas in
# the current viewstore array. 
def deleteaddr(targetaddr):
    print("Initializing delete and broadcast...")
    payload = {"socket-address": targetaddr}
    if targetaddr in viewstore:
        # Delete from local view
        print("trying to sending a delete to own view")
        try:

            request = req.delete("http://" + socketaddr + "/key-value-store-view", data=payload,
                                 timeout=timeoutduration)
            print("success with a response: ", request)
        except req.exceptions.RequestException as ex:
            print("sending viewstore request to self failed....")
        # Broadcast to other views.
        print("  - Broadcasting DELETE to other replicas...")

        #	DEBUG PAYLOAD PRINT

        for replicaaddr in viewstore:
            if replicaaddr != socketaddr:
                print("  - Sending DELETE to " + replicaaddr + "...")
                try:
                    request = req.delete("http://" + replicaaddr + "/key-value-store-view", data=payload,
                                         timeout=timeoutduration)
                    print("request view-delete debug", request)
                    print("   - Successful")
                except req.exceptions.RequestException as ex:
                    # WARNING, a replica in the view could not be reached! time to call key-value-store-view DELETE.
                    print("   WARNING - Unable to reach replica address " + replicaaddr + "! Exception Raised: ", ex)
                    # Recursion can happen if more than
                    # one replica address goes down.
                    deleteaddr(replicaaddr)

#Calculates total differences between current clock and received metadata clock
#Expected clock
def calculateDifferences(combined_clock):
	total_differences = 0
	for a,b in combined_clock:

		if a != b:
			total_differences += abs( int(a) - int(b) )
		
	return total_differences

"""
this differentiates the replicas from one another
and can use this information to automatically update
causalmetadata per requests

  This works by finding the replica's socket address in a list of the view addresses
  each replica should have its own socket address variable per docker env
"""

def getReplicaNumber():
    global viewstore
    viewstoreList = viewstore
    print("view: ", viewstoreList)
    print("socketaddress: ", socketaddr)
    global replicaNumber
    global NumberOfReplicas
    view = checkOtherViews(viewstoreList)
    if view != None:
        print("\n\nNEW VIEW")
        print("view: ", view, "\n\n")

    for address in viewstoreList:
        if address == socketaddr:
            addressIndex = viewstoreList.index(address)
            print("found replica number at list index: ", viewstoreList.index(address), "\n\n")
            replicaNumber = addressIndex
            print("New Replica Number: ", replicaNumber)
    return replicaNumber


def checkOtherViews(viewstore):
    print("view store: ", viewstore)
    storedViews = []
    differentViews = []
    for address in viewstore:
        if address != socketaddr:
            try:
                print("trying to check the view")
                checkView = req.get("http://" + address + "/key-value-store-view", timeout=1)
                checkView = checkView.json()
                responseView = checkView['view']
                responseView = responseView.split(",")
                print("response view: ", responseView)
                storedViews.append(responseView)
            except req.exceptions.RequestException as ex:
                print("Could not access view... moving on")

    print("stored views: ", storedViews)
    for views in storedViews:
        if len(viewstore) < len(views):
            print(viewstore, " is less than: ", views)
            differentViews.append(views)
        elif len(viewstore) > len(views):
            print("current views is greater than others, need to PUT itself")
            for missView in views:
                payload = {"socket-address": socketaddr}
                try:
                    print("trying to PUT itself to other views")
                    putView = req.put("http://" + missView + "/key-value-store-view", payload, timeout=1)
                    print("response from PUT'ing itself in other views: ", putView.json())
                except req.exceptions.RequestException as xd:
                    print("Could not PUT itself to other views...")
    if len(differentViews) != 0:
        view = differentViews[0]
        return view
    else:
        return None

"""
Checks to see if a replica is behind in terms of causal consitancy. If it is, the replica should send a GET request 
to other replicas and compare vector clock. If the returned vector clock is equal to the current vector clock, 
replica should update the it's KVS with that replica's version of it. If the replica is behind, it should return the KVS
dictionary, otherwise it should return None.
"""
def checkVersion(currentCopy, receivedCopy, replicaNumber, key):
	differencesIndex = {}
	seen = []
	for i in range(len(currentCopy)):
		if currentCopy[i] != receivedCopy[i]:
			#replica was down, request may be wrong
			print("Here is the data: ")
			if currentCopy[i] < receivedCopy[i]:
				print("current replica is behind")
				differencesIndex[replicaNumber] = 'behind'
				vc = receivedCopy
				# iterate through the replicas
				for address in viewstore:
					# if we are not dealing with the replica responding to the client
					if address != socketaddr:
						# We want to extract the vector clock
						try:
							r = req.get("http://" + address +"/key-value-store/"+str(key), timeout=5)
							if r.status_code != 404:
								vc = r.json()['causal-metadata']

								# if the vector clock matches, return the KVS of that replica
								kvs = None
								if vc.split(" ") == receivedCopy:
									print("Sending GET to", address)
									try:
										kvs = req.get("http://" + address + "/kvs", timeout=5)
										print(kvs.json())
									except req.exceptions.RequestException as ex:
										print("could not get from ", address, ex)

								# print("When trying to update replica, failed to reach replica:", address)
								# iterate through each key in the replica's dictionary and update each key in the current replica's dictionary
								# basically get it up to date
								if kvs:
									for key in kvs.json():
										dic[key] = kvs.json()[key]
								print("DIC:", dic)
						except req.exceptions.RequestException as ex:
							print("could not get from ", address, ex)
						#this replica contains the key value store and is not behind
				if vc == receivedCopy:
					return vc
				else:
					return vc.split(" ")
			else:
				print("current replica is advanced??? returning -99")
				differencesIndex[replicaNumber] = 'advanced'
				return -99
	return None

# /key-value-store - same as assignment2, however also includes 
# forwarding functionality for PUT and DELETE.
class Store(Resource):
	def get(self,key):
		print("(Log Message)[STORE] Initiating get!")
		print("causal: ", causalmetadata, flush=True)
		print("length of causal: ", len(causalmetadata.split(" ")), flush=True)
		print("length of viewstore: ", len(viewstore), flush=True)
		if key not in dic:
			return {'doesExist':False,'error':'Key does not exist','message':'Error in GET'}, 404
		return {"message":"Retrieved successfully", "causal-metadata": causalmetadata, "value": dic.get(key)}, 200

	def put(self,key):
		global causalmetadata
		global total_differences
		replicaNumber = getReplicaNumber()
		#replicaNumber is the index of the current replica
		#we can use this with the vector clock to figure out which one

		print("(Log Message)[STORE] Initiating put!")
		parser = reqparse.RequestParser(bundle_errors=True)
		parser.add_argument("value", type=str)
		parser.add_argument("causal-metadata", type=str)
		parser.add_argument("node", type=int)
		args = parser.parse_args()
		print(args, flush=True)

		#parsing meta-data
		meta = str(args["causal-metadata"])

		#case of first put
		#other requests with an empty causal-metadata is illegal
		if meta == "":
			meta = causalmetadata

		print("meta: ", meta,flush=True)
		received_clock = meta.split(" ")
		print("received clock: ", received_clock, flush=True)
		current_clock = causalmetadata.split(" ")
		print("current_clock: ", current_clock, flush=True)
		print("replicaNumber: ", replicaNumber)

		#Soft copy of the vector clocks to work with
		currentCopy = current_clock.copy()
		receivedCopy = received_clock.copy()
		print("Current copy, received copy:", currentCopy, receivedCopy)
		updatedVC = checkVersion(currentCopy, receivedCopy, replicaNumber, key)
		print("updated VC", updatedVC)

		# set the current clock to the clock received from the other replica
		if updatedVC:
			current_clock = updatedVC
		
		# There was no conflict, set the indivdual replica clock
		clock = int(current_clock[replicaNumber])
		
		newclock = int(received_clock[replicaNumber])
		
		#make sure the nodes are not incrementing their own VC's, only the replica comminucating with the client should increment
		if not args["node"]:
			clock +=1
			current_clock[replicaNumber] = str(clock)

		#then update point-wise max
		updated_clock = [max(value) for value in zip(current_clock, received_clock)]
		print("updated clock:", updated_clock,flush=True)

		causalmetadata = " ".join(updated_clock)

		#incorrect input
		if not args["value"]:
			res = {'error':'Value is missing', 'message': 'Error in PUT'}
			return res,400
		
		#key is too long
		if len(key)>50:
			res = {'error':'Key is too long', 'message': 'Error in PUT'}
			return res,400

		# Confirmed to be a valid key! Create a newkey boolean and either add or replace.
		newkey = False
		if key not in dic:
			dic[key] = args["value"]
			res = {"message":"Added successfully", "causal-metadata": causalmetadata}
			newkey = True
		else:
			# To prevent constant repetitive put calls between replicas...
			originalvalue = dic.get(key)
			if originalvalue == args["value"]:
				# Nothing changed. Do not broadcast.
				res = {"message":"Updated successfully", "causal-metadata": causalmetadata}
				return res,200
			else:
				# Something changed!
				dic[key] = args["value"]
				res = {"message":"Updated successfully", "causal-metadata": causalmetadata}
				newkey = False
		
		
		# Regardless of newkey, Broadcast the same PUT request to all other replicas in the view save for itself.
		valuevar = str(args["value"])
		payload = {"value" : valuevar, "causal-metadata": causalmetadata, "node":1}
		

		# only the the replica dealing with the client should broadcast the request
		if not args["node"]:
			print("  - Broadcasting PUT to other replicas...")
			print("Payload: ", payload)
			for replicaaddr in viewstore:
				if replicaaddr != socketaddr:
					print("  - Sending PUT to " + replicaaddr + "...")
					try:
						request = req.put("http://" + replicaaddr +"/key-value-store/"+str(key), data=payload, timeout=timeoutduration)
						print("   - Success!")
					except req.exceptions.RequestException as ex:
						# WARNING, a replica in the view could not be reached! time to call key-value-store-view DELETE.
						print("   WARNING - Unable to reach replica address " + replicaaddr + "! Exception Raised: ", ex)
						deleteaddr(replicaaddr)
			# End Broadcast
			if newkey:
				return res,201
			else:
				return res,200

	def delete(self,key):
		print("(Log Message)[STORE] Initiating delete!")

		global causalmetadata
		global total_differences
		replicaNumber = getReplicaNumber()
		#replicaNumber is the index of the current replica
		#we can use this with the vector clock to figure out which one
		parser = reqparse.RequestParser(bundle_errors=True)
		parser.add_argument("causal-metadata", type=str)
		parser.add_argument("node", type=int)
		args = parser.parse_args()
		print(args, flush=True)

		#parsing meta-data
		meta = str(args["causal-metadata"])

		#case of first put
		#other requests with an empty causal-metadata is illegal
		if meta == "":
			meta = causalmetadata

		print("meta: ", meta,flush=True)
		received_clock = meta.split(" ")
		print("received clock: ", received_clock, flush=True)
		current_clock = causalmetadata.split(" ")
		print("current_clock: ", current_clock, flush=True)
		print("replicaNumber: ", replicaNumber)

		#Soft copy of the vector clocks to work with
		currentCopy = current_clock.copy()
		receivedCopy = received_clock.copy()
		print("Current copy, received copy:", currentCopy, receivedCopy)
		updatedVC = checkVersion(currentCopy, receivedCopy, replicaNumber, key)
		print("updated VC", updatedVC)

		# replica was behind, set the current clock to the clock received from the other replica
		if updatedVC:
			current_clock = updatedVC
		
		# There was no conflict, set the indivdual replica clock
		clock = int(current_clock[replicaNumber])
		
		newclock = int(received_clock[replicaNumber])
		
		#make sure the nodes are not incrementing their own VC's, only the replica comminucating with the client should increment
		if not args["node"]:
			clock +=1
			current_clock[replicaNumber] = str(clock)

		#then update point-wise max
		updated_clock = [max(value) for value in zip(current_clock, received_clock)]
		print("updated clock:", updated_clock,flush=True)

		causalmetadata = " ".join(updated_clock)

		#key is too long
		if len(key)>50:
			res = {'error':'Key is too long', 'message': 'Error in DELETE'}
			return res,400
		
		payload = {"causal-metadata": causalmetadata, "node":1}
		if key in dic:
			del dic[key]

			# Broadcast same DELETE request to all other replicas in the view save for itself.
			if not args["node"]: 
				print("  - Broadcasting DELETE to other replicas...")
				for replicaaddr in viewstore:
					if replicaaddr != socketaddr:
						print("  - Sending DELETE to " + replicaaddr + "...")
						try:
							#TO-DO change req.delete to a request("delete", URL, data = metadata, timeout=timeoutduration)
							request = req.delete("http://" + replicaaddr + "/key-value-store/" + str(key), data = payload, timeout=timeoutduration)
							print("   - Done!")
						except Timeout as ex:
							# WARNING, a replica in the view could not be reached! time to call key-value-store-view DELETE.
							print("   WARNING - Unable to reach replica address " + replicaaddr + "! Exception Raised: ", ex)
							deleteaddr(replicaaddr)

				return {"message":"Deleted successfully", "causal-metadata": causalmetadata},200
		
		if key not in dic:
			return {'doesExist': False,'error':'Key does not exist','message':'Error in DELETE'},404

# /key-value-store-view
class ViewStore(Resource):
	def get(self):
		print("(Log Message)[VIEWSTORE] Initiating get!")
		viewstring = ','.join(viewstore)
		return {"message":"View retrieved successfully","view":viewstring}, 200

	def put(self):
		global causalmetadata
		global replicaNumber
		print("(Log Message)[VIEWSTORE] Initiating put!")
		parser = reqparse.RequestParser(bundle_errors=True)
		parser.add_argument("socket-address", type=str)
		args = parser.parse_args()

		targetsocket= args["socket-address"]
		if targetsocket in viewstore:
			return {"error":"Socket address already exists in the view","message":"Error in PUT"},404
		else:
			viewstore.append(targetsocket)
			viewstore.sort()
			causaldata = causalmetadata.split(" ")
			print("causal data after adding new replica: ", causaldata, flush=True)
			if len(causaldata) < len(viewstore):
				print("There is more replicas than causaldata")
				causaldata.append("0")
				causalmetadata = " ".join(causaldata)
				print("new causalmetadata: ", causalmetadata, flush=True)
			return {"message":"Replica added successfully to the view"}, 201

	def delete(self):
		global causalmetadata
		global replicaNumber
		print("(Log Message)[VIEWSTORE] Initiating delete!")
		parser = reqparse.RequestParser(bundle_errors=True)
		parser.add_argument("socket-address", type=str)
		args = parser.parse_args()

		targetsocket= args["socket-address"]
		if targetsocket in viewstore:
			viewstore.remove(targetsocket)
			return {"message": "Replica deleted successfully from the view"}, 200
	
		return {"error":"Socket address does not exist in the view","message":"Error in DELETE"}, 404

# Used to get the KVS of a replica
class KVS(Resource):
	def get(self):
		return dic

# Used to sync a replica in the case that it goes down and comes back online
def instantiateReplica(viewstore, socketaddress):
	missingTheReplica = []
	getaddress = None
	for address in viewstore:
		print("Working on: ", address)
		if address != socketaddress:
			print("trying to broadcast...")
			try:
				response = req.get("http://" + address + "/key-value-store-view", timeout=10)
				response = response.json()
				print("response: ", response)
				responseView = response['view']
				for views in responseView:
					if socketaddress not in views:
						if address not in missingTheReplica:
							missingTheReplica.append(address)

			except req.exceptions.RequestException as ex:
				print("Could not get a response")
				print("It may be first run of the program OR something is terribly wrong")
	print("Replicas that need it: ", missingTheReplica)

	#To insert the replica's view to the ones that need it
	for addy in missingTheReplica:
		try:
			#Tries to send a request
			print("Sending put request....to: ", addy)
			payload = {"socket-address" : socketaddress}
			requestReplica = req.put("http://" + addy + "/key-value-store-view", data=payload, timeout=10)
			print("Reponse received from ", addy, " and response is: ", requestReplica.json())
		except req.exceptions.RequestException as xd:
			print("Could not send a PUT to ", addy)
			print("Boohoo :(")
	#now get the KVS data
	print("getting KVS from a replica")
	getaddress = viewstore[0]
	print("dictionary: ", dic, flush=True)
	if not dic:
		try:
			for address in viewstore:
				if address != socketaddress:
					kvs = req.get("http://" + address + "/kvs", timeout=5)
					kvs = kvs.json()
					print("Request response from kvs: ", kvs, flush=True)
					if kvs:
						print("adding dictionary from KVS to self")
						for key in kvs:
							dic[key] = kvs[key]
		except req.exceptions.RequestException as rex:
			print(rex, flush=True)
			print("Could not GET to the first replica in viewstore", flush=True)
	print("SUCCESS!")

def getShards():
	return list(hr.get_nodes())

if __name__ == "__main__":
	print("Hello there! CSE_Assignment3 replica instance initiated...")

	#to ensure that the thread running has the required environment variables
	if 'SOCKET_ADDRESS' in os.environ and os.environ['SOCKET_ADDRESS'] != "":
		socketaddr = os.environ['SOCKET_ADDRESS']
		print("SOCKET_ADDRESS Docker env variable found! read: ", socketaddr)
	else:
		print("[ERROR] SOCKET_ADDRESS Docker env variable not found!")

	print("socket addr: ", socketaddr, flush=True)


	#to ensure that the thread running has the required environment variables
	if 'VIEW' in os.environ and os.environ['VIEW'] != "":
		view = os.environ['VIEW']
		viewstore = view.split(",")
		print("VIEW Docker env variable found! read: ", viewstore)
	else:
		print("[ERROR] VIEW Docker env variable not found!")

	#check to see if shard count is present. If not, we know that the node was not instantiated on startup
	if "SHARD_COUNT" in os.environ and os.environ["SHARD_COUNT"]!="":
		shardcnt = os.environ["SHARD_COUNT"]
	
	clock = []
	for addy in viewstore:
		clock.append("0")

	causalmetadata = " ".join(clock)
	# Main instance. 
	print(" Starting replica instance...")
	ip_add = "0.0.0.0"

	hr = HashRing()

	#add the shards to the hashring
	for i in range(int(shardcnt)):
		hr.add_node("shard"+str(i))
	
	#shards created are
	print("shards created are", getShards())

	instantiateReplica(viewstore, socketaddr)
	print("instatiating..")

	

	api.add_resource(Store, "/key-value-store/<key>")
	api.add_resource(ViewStore, "/key-value-store-view")
	api.add_resource(KVS, "/kvs")
	app.run(host=ip_add,port=8085)