# assignment4.py
#
# Contains the implementation of a replicated fault-tolerant
# key-value store that provides casual consistency.

from flask import Flask, jsonify, request, abort
from flask_restful import Api, Resource, reqparse
from requests.exceptions import Timeout
from uhashring import HashRing
from mmh3 import hash as m3h
import json
import os
import requests as req
import numpy as np
import time



app = Flask(__name__)
api = Api(app)

# Tweak variable, timeout until a replica is considered failed. 
# Only for accessing other replicas!
timeoutduration = 1

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
shards = []
shard_id = None
shard_members = {}

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

#Sort socket address/view addresses helper functions
def isolateIP(ip):
    return tuple(int(part) for part in ip.split('.'))

def sort_key(item):
    return isolateIP(item[0])



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

        shardid = getShardID(key,hr)
        print(shardid, flush=True)
        if shardid != shard_id:
            print("Caught wrong GET shardid", flush=True)
            nodelist = shard_members[shardid]
            fwdget = None
            #Try to forward the GET to each node, if one succeeds, break loop
            for node in nodelist:
                try:
                    fwdget = req.get("http://" + node +"/key-value-store/"+str(key), timeout=timeoutduration)
                    if fwdget.status_code == 200 or fwdget.status_code == 201:
                        break
                except:
                    print("   WARNING - Unable to reach replica address " + replicaaddr + "! Exception Raised: ", ex)
            if fwdget != None:
                return fwdget.json(), fwdget.status_code

        if key not in dic:
            return {'doesExist':False,'error':'Key does not exist','message':'Error in GET'}, 404
        return {"message":"Retrieved successfully", "causal-metadata": causalmetadata, "value": dic.get(key)}, 200


        # else:
        #     #send request
        #     # nodelist = shard_members['shardid']
        #     # nodetosend = nodelist[0]
            
        #     # payload = {"message":"Retrieved successfully", "causal-metadata": causalmetadata, "value": dic.get(key)}
        #     return 

    def put(self,key):
        global causalmetadata
        global total_differences
        replicaNumber = getReplicaNumber()
        #replicaNumber is the index of the current replica
        #we can use this with the vector clock to figure out which one
        parser = reqparse.RequestParser(bundle_errors=True)
        parser.add_argument("value", type=str)
        parser.add_argument("causal-metadata", type=str)
        parser.add_argument("node", type=int)
        args = parser.parse_args()
        print(args, flush=True)

        # parsing meta-data
        meta = str(args["causal-metadata"])
        #catch wrong shard-id requests here
        shardid = getShardID(key, hr)
        if shardid != shard_id:
            #caught the wrong shard_id in PUT
            print("Caught wrong shardid, forwarding...")
            nodelist = shard_members[shardid]
            valuevar = str(args["value"])
            payload = {"value": valuevar, "causal-metadata": meta}
            fwdRequest = None

            for node in nodelist:
                try:
                    print("Trying to forward PUT to ", node)
                    fwdRequest = req.put("http://" + node + "/key-value-store/" + str(key), data=payload,timeout=timeoutduration)
                    if fwdRequest.status_code == 200 or fwdRequest.status_code == 201:
                        break

                except req.exceptions.RequestException as ex:
                    print("   WARNING - Unable to reach replica address " + node + "! Exception Raised: ", ex)
                    deleteaddr(node)
            if fwdRequest != None:
                #PUT forward was successful
                print(fwdRequest.json())
                #The original receiving node should return the result of the forward
                return fwdRequest.json(), fwdRequest.status_code
            else:
                #PUT Forward failed
                return "Could not forward the PUT to the correct shard", 500



        print("(Log Message)[STORE] Initiating put!")


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
        print("current node clock: ", clock)

        newclock = int(received_clock[replicaNumber])

        #make sure the nodes are not incrementing their own VC's, only the replica comminucating with the client should increment

        if not args["node"]:
            print("Update the clock")
            clock +=1
            print("incremented clock: ", clock)
            current_clock[replicaNumber] = str(clock)

        #then update point-wise max
        updated_clock = [max(value) for value in zip(current_clock, received_clock)]
        print("updated clock:", updated_clock,flush=True)

        causalmetadata = " ".join(updated_clock)

        #get the shard id of the given key
        shardid = getShardID(key,hr)
        print(shardid, flush=True)

        #if both shard id's match, continue with request normally. Otherwise, forward request to a node with the same shardid
        #as the key
            
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
            res = {"message":"Added successfully", "causal-metadata": causalmetadata, "shard-id":shard_id}
            newkey = True
        else:
            # To prevent constant repetitive put calls between replicas...
            originalvalue = dic.get(key)
            if originalvalue == args["value"]:
                # Nothing changed. Do not broadcast.
                res = {"message":"Updated successfully", "causal-metadata": causalmetadata, "shard-id":shard_id}
                return res,200
            else:
                # Something changed!
                dic[key] = args["value"]
                res = {"message":"Updated successfully", "causal-metadata": causalmetadata, "shard-id":shard_id}
                newkey = False


        # Regardless of newkey, Broadcast the same PUT request to all other replicas in the view save for itself.
        valuevar = str(args["value"])
        payload = {"value" : valuevar, "causal-metadata": causalmetadata, "node":1}


        # only the the replica dealing with the client should broadcast the request
        if not args["node"]:
            print("  - Broadcasting PUT to other replicas...")
            print("Payload: ", payload)
            for replicaaddr in shard_members[shardid]:
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


        #catch wrong delete here
        shardid = getShardID(key, hr)
        if shardid != shard_id:
            # caught the wrong shard_id in PUT
            print("Caught wrong shardid, forwarding...", flush=True)
            nodelist = shard_members[shardid]
            valuevar = str(args["value"])
            payload = {"value": valuevar, "causal-metadata": meta}
            fwdRequest = None
            #try for all nodes in the shard, if one succeeds, break loop
            for node in nodelist:
                try:
                    print("Trying to forward DELETE to ", node)
                    fwdRequest = req.delete("http://" + node + "/key-value-store/" + str(key), data=payload,
                                         timeout=timeoutduration)
                    if fwdRequest.status_code == 200 or fwdRequest.status_code == 201:
                        break

                except:
                    print("   WARNING - Unable to reach replica address " + node + "! Exception Raised: ", ex)
                    deleteaddr(node)
            if fwdRequest != None:
                # PUT forward was successful
                print(fwdRequest.json())
                # The original receiving node should return the result of the forward
                return fwdRequest.json(), fwdRequest.status_code
            else:
                # PUT Forward failed
                return "Could not forward the DELETE to the correct shard", 500

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
        shardid = getShardID(key,hr)
        print(shardid, flush=True)

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
            for replicaaddr in shard_members[shardid]:
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

            return {"message":"Deleted successfully", "causal-metadata": causalmetadata, "shard-id":shard_id},200

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
            viewstore.sort(key = sort_key)
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

class ShardIDs(Resource):
    #return all shard id's
    def get(self):
        return {"message":"Shard IDs retrieved successfully","shard-ids": getShards()},200

# Pretty self-explanatory - Returns any given node's shard_id. 
class NodeShardId(Resource):
    def get(self):
        print("(Log Message)[SHARD] Initiating node-shard-id GET!")
        return {"message":"Shard ID of the node retrieved successfully","shard-id":shard_id}, 200

# Aux service not specified in the spec, for use in ShardIdMembers.
# For any node, return its shard_members. 
class NodeShardMembers(Resource):
    def get(self):
        print("(Log Message)[SHARD] Initiating node-shard-members GET!")
        return {"message":"Shard Members of the node retrieved successfully","shard-id-members":shard_members}, 200

# Aux service not specified in spec, for use in ShardReshard.
# For any node, re-set it's shardID.
class NodeSetShardId(Resource):
    def put(self):
        #{"shard-id": id,"shards": shards, "shard-members":shard_members[id]}
        print("(Log Message)[SHARD] Initiating node-set-shard-id PUT!")
        parser = reqparse.RequestParser(bundle_errors=True)
        parser.add_argument("shard-id", type=str)
        parser.add_argument("shards", type=str)
        parser.add_argument("shard-members", type=str)
        args = parser.parse_args()
        newshardID = args["shard-id"] # get shard-id
        json_acceptable_string = args["shards"].replace("'", "\"")
        d = json.loads(json_acceptable_string)
        newshards = d # get shard-id
        json_acceptable_string = args["shard-members"].replace("'", "\"")
        d = json.loads(json_acceptable_string)
        newshard_members = d # get shard-id
        global shard_id 
        global shards
        global shard_members
        print("Replacing current shard_id " + str(shard_id) + " with newshardID " + str(newshardID))
        shard_id = newshardID # update.
        print("Replacing current shards " + str(shards) + " with newshards " + str(newshards))
        shards = newshards # update.
        print("Replacing current shard_members" + str(shard_members) + " with newshard_members " + str(newshard_members))
        shard_members = newshard_members # update.
        return {"message":"Updated successfully"}, 200

# Aux service not specified in the spec, for use in ShardReshard. 
# For any node, given the passed JSON object in the PUT call, 
# overwrite the existing dictionary. 
class KVSOverwite(Resource):
    def put(self):
        global dic
        print("(Log Message)[SHARD] Initiating KVSOverwrite PUT!")
        parser = reqparse.RequestParser(bundle_errors=True)
        parser.add_argument("kvs")
        args = parser.parse_args()
        kvs = args["kvs"] # get kvs to overwrite existing dictionary. 
        if kvs is None:
            kvs = {}
            print("Changing none payload to {}!")
        print("Overwiting existing dictionary:\n" + str(dic) + "\nwith passed kvs:\n" + str(kvs))
        dic = kvs
        return {"message":"Updated successfully"}, 200

# Given any shard-id, gets all of the members of that shard-id. 
# Mechanism:
# = Check if current shard is the requested shard. if so, return shard_members. 
# = Else, we have to find a node that is part of the shard. Iterate over all
# view socket addresses that are not present in shard_members, executing
# GET NodeShardId for each socket address until the answer matches with requested.
# = With that same address, execute GET NodeShardIdMembers (This is an aux service
# not specified in the spec.)
class ShardIdMembers(Resource):
    #URL Format: "/key-value-store-shard/shard-id-members/<shard-id>"
    def get(self, id):
        print("(Log Message)[SHARD] Initiating shard-id-members GET for id " + str(id))
        if id == shard_id:
            print("(Log Message)[SHARD] passed id is the same as host node's id!")
            print("Current shard_members:")
            print(shard_members)
            return {"message":"Members of shard ID retrieved successfully","shard-id-members":shard_members[id]}, 200
        else:
            print("(Log Message)[SHARD] passed id is NOT the same as host node's id, beginning search.")
            for replicaaddr in viewstore:
                if replicaaddr not in shard_members:
                    print("  - Sending GET to " + replicaaddr + "/key-value-store-shard/node-shard-id...")
                    try:
                        request = req.get("http://" + replicaaddr +"/key-value-store-shard/node-shard-id", timeout=timeoutduration)
                        print("   - Success! Got a response of " + str(request.text))
                        data = request.json()
                        if data['shard-id'] == id:
                            print('Got a matching shard-id!')
                            print("  - Sending GET to " + replicaaddr + "/key-value-store-shard/node-shard-members...")
                            # Found a hit! Get this address's shard members. 
                            request = req.get("http://" + replicaaddr +"/key-value-store-shard/node-shard-members", timeout=timeoutduration)
                            print("   - Success! Got a response of " + str(request.text))
                            data = request.json()
                            # All done! Return it. 
                            print("...(Log Message)[SHARD] shard-id-members GET is done!")
                            return {"message":"Members of shard ID retrieved successfully","shard-id-members":data['shard-id-members'][id]}, 200
                    except req.exceptions.RequestException as ex:
                        # WARNING, a replica in the view could not be reached! time to call key-value-store-view DELETE.
                        print("   WARNING - Unable to reach replica address " + replicaaddr + "! Exception Raised: ", ex)
                        deleteaddr(replicaaddr)
            print("ERROR - passed shard does not exist?!")
            return {"error":"Shard address does not exist!","message":"Error in GET"}, 404

class ShardKeyCount(Resource):
    #URL Format: "/key-value-store-shard/shard-id-key-count/<shard-id>"
    # return # of keys stored in the shard
    def get(self, id):
        numKeys=0
        for replicaaddr in shard_members[id]:
            #Making sure the replica is up
            if replicaaddr in viewstore:
                replica_dic = (req.get("http://" + replicaaddr +"/kvs", timeout=timeoutduration)).json()
                #print("replica dic:",replica_dic)
                for key in replica_dic:
                    #print("key",key)
                    numKeys+=1
                return {"message":"Key count of shard ID retrieved successfully","shard-id-key-count":numKeys},200  
        
        #No replicas were up
        return {"No replicas up in the shard"},500
        

#TO-DO:
# This only puts to the specific node-socket-address receiving the PUT request
# Need to broadcast this put to the entire VIEW or only the shard
class ShardAddMember(Resource):
    #URL Format: "/key-value-store-shard/add-member/<shard-id>"
    # takes a PUT request and adds the socket-address to a shard
    def put(self, id):
        shardid = id
        parser = reqparse.RequestParser(bundle_errors=True)
        parser.add_argument("socket-address", type=str)
        args = parser.parse_args()

        targetsocket = args["socket-address"]
        if targetsocket not in viewstore:
            print("[WARNING]: socket address of the node is currently not in the view", flush=True)
        if shardid not in shards:
            return {"message":"ERROR: shard-id does not exist"}, 404
        if targetsocket not in shard_members[shardid]:
            shard_members[shardid].append(targetsocket)
            broadcastload = {"socket-address":targetsocket}
            for member in viewstore:
                if member != socketaddr and member != targetsocket:
                #broadcast to all shards
                    try:
                        addshardtomem = req.put("http://" + member + "/key-value-store-shard/add-member/"+shardid, data=broadcastload, timeout=5)
                    except:
                        print("could not add new member to ", member)
                        continue

            payload = {"shard-id":shardid, "socket-address": socketaddr}
            print("ShardAddMember payload: ", payload, flush=True)
            try:
                addshard = req.put("http://" + targetsocket +"/addtoshard", data=payload, timeout=5)
            except:
                print("Could not add to shard")
            return 200
        else:
            return {"message":"Node is already in the shard"}, 200


# Resharding class - to be excecuted by the administrator and not
# done automatically, in two cases: either 1) there is only 1
# node left in a shard or 2) new nodes have been added. Can
# be used to change the shard-count. 

# Takes a PUT request, expecting JSON content {"shard-count":<shard-count>}.

# Mechanism design:
# = Collects all dictionaries from all different existing shards and gathers
# them in one dictionary.
# = Reshards using the existing assignshards() class
# = Redistributes keys 
class ShardReshard(Resource):
    def put(self):
        global shard_id
        global shards
        print("(Log Message)[SHARD] Initiating reshard PUT!")
        parser = reqparse.RequestParser(bundle_errors=True)
        parser.add_argument("shard-count", type=str)
        args = parser.parse_args()
        shardcnt= args["shard-count"]
        numberOfNodes = len(viewstore)

        if int(shardcnt) > numberOfNodes or numberOfNodes // int(shardcnt) < 2:
            return {"message":"Illegal reshard"},400


        # **Collect all dictionaries from shards other than own shard (which node irrelevant)**

        # Create dictionary of all keys and values, initalized by current shard dictionary.
        aggregatedic = dic
        # With list of all shard ID's, shards, iterate.
        for id in shards:
            if id != shard_id: # If not current shard
                print("Querying dictionary for shard '" + str(id)+"'.")
                # For each shard, get list of all nodes in shard by calling same node's class function.
                request = ShardIdMembers.get(self, id)
                id_shard_members = request[0]['shard-id-members']
                print("Got id_shard_members:")
                print(id_shard_members)
                replicaaddr = id_shard_members[0] # It doesn't matter which address we go for in the shard.
                # Get all keys (KVS resource) from any node in the shard. Append to current dictionary.
                print("  - Sending GET to " + replicaaddr + "/kvs...")
                try:
                    request = req.get("http://" + replicaaddr +"/kvs", timeout=timeoutduration)
                    json_acceptable_string = request.text.replace("'", "\"")
                    d = json.loads(json_acceptable_string)
                    print("   - Success! Got a response of " + str(d))
                    # Append gotten key-value store to current dictionary
                    aggregatedic = {**aggregatedic, **d}
                    print("Dic of shard "+ str(id) + " added. Current combined dictionary: " + str(aggregatedic))
                except req.exceptions.RequestException as ex:
                    # WARNING, a replica in the view could not be reached! time to call key-value-store-view DELETE.
                    print("   WARNING - Unable to reach replica address " + replicaaddr + "! Exception Raised: ", ex)
                    deleteaddr(replicaaddr)
        print("Complete dictionary gathered!")
        
        # **Reshard existing shards.**

        # Copied code from initialization. 
        # Reset current information for this node. 
        shards = []
        shard_id = None
        shard_members = {}
        nodes = None

        if shardcnt != "":
            #Get node index from view list
            nodeidx = viewstore.index(socketaddr)
            #split the nodes in the viewlist into shards of index 0 to n
            nodes = assignShards(viewstore,shardcnt)
            #cannot partition nodes into shards if it cannot have at least 2 nodes per shard
            if nodes == -1:
                print("Error stated above")
                return {"message":"Not enough nodes to provide fault-tolerance with the given shard count!"}, 400
            else:
                #range starts from 0
                for shard in range(int(shardcnt)):
                    if socketaddr in nodes[shard]:
                        shard_id = shard
        hr = HashRing()
        #add the shards to the hashring
        for i in range(int(shardcnt)):
            hr.add_node("shard"+str(i))
            shards.append("shard"+str(i))
        #assign all addresses to a shard
        #if socketaddr is part of the shard, then assign shardid to the node
        if nodes != None:
            for i in range(int(shardcnt)):
                shard_members["shard"+str(i)] = nodes[i]
                if socketaddr in shard_members["shard"+str(i)]:
                    shard_id = "shard"+str(i)
        #End copied code. 
        print("Shard master list completed. New Schema now is: " + str(shard_members))# *****NOT COPIED

        # Apply effects to ALL other shards!
        for id in shards: 
            payload = {"shard-id": str(id),"shards": str(shards), "shard-members": str(shard_members)}
            # For each shard, get list of all nodes in shard by calling same node's class function.
            for replicaaddr in shard_members[id]:
                print("  - Sending PUT to " + replicaaddr + "/key-value-store-shard/node-set-shard-id with payload "+str(payload)+"...")
                try:
                    request = req.put("http://" + replicaaddr +"/key-value-store-shard/node-set-shard-id", data=payload, timeout=timeoutduration)
                    print("   - Success!")
                except req.exceptions.RequestException as ex:
                    # WARNING, a replica in the view could not be reached! time to call key-value-store-view DELETE.
                    print("   WARNING - Unable to reach replica address " + replicaaddr + "! Exception Raised: ", ex)
                    deleteaddr(replicaaddr)
        print("All replicas have been updated to revised shard scheme!")

        # **Redistribute keys, overwriting existing.**

        # Requires SAME key-to-shard mechanism implemented in the PUT call for key-value-store. 
        # Can copy that code down here and use it to create an array of dictionaries to apply to
        # each shard, and then call the auxiliary functions to overwrite and apply the devised 
        # schema.  
        sharddic = {} # master dictionary containing all dictionaries seprated by schema
        for id in shards:
            sharddic[id] = {} # Append an empty dictionary for each shard id. 
        # Dictionary should now look something like: {'shard1':{},'shard2':{}}
        for key, value in aggregatedic.items(): # For each key/value pair
            shardID = getShardID(key, hr) # Apply the same schema as the PUT call
            sharddic[shardID][key] = value # Insert into dictionary for that shardID, ex) {'shard1':{'key':'value'},'shard2':{}}
        print("sharddic has been completed. It looks like: " + str(sharddic))

        for id in shards: 
            print("For shardid " + str(id) + ", inserting kvs of " + str(sharddic[id]))
            payload = {"kvs": sharddic[id]}
            # For each shard, get list of all nodes in shard by calling same node's class function.
            for replicaaddr in shard_members[id]:
                print("  - Sending PUT to " + replicaaddr + "...")
                try:
                    request = req.put("http://" + replicaaddr +"/kvs/overwrite", data=payload, timeout=timeoutduration)
                    print("   - Success!")
                except req.exceptions.RequestException as ex:
                    # WARNING, a replica in the view could not be reached! time to call key-value-store-view DELETE.
                    print("   WARNING - Unable to reach replica address " + replicaaddr + "! Exception Raised: ", ex)
                    deleteaddr(replicaaddr)

        # All done!
        return {"message":"Resharding done successfully"}, 200

# Used to sync a replica in the case that it goes down and comes back online
def instantiateReplica(viewstore, socketaddress):
    missingTheReplica = []

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
    print("dictionary: ", dic, flush=True)
    if not dic:
        if shard_id != None:
            try:
                for address in shard_members[shard_id]:
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
        else:
            print("Not part of a shard yet, Did not get any KVS information", flush=True)
    print("SUCCESS!")

def getShards():
    return list(hr.get_nodes())

def getShardID(key, hashRing):
    return hashRing.get_node(key)


def assignShards(viewlist,shardCount):
    shardcount = int(shardCount)
    length = len(viewlist)
    if length // int(shardCount) == 1:
        print("Illegal sharding, must have at least two nodes per shard")
        return -1
    viewlist = np.array_split(viewlist,shardcount)
    print("view list split..: ", viewlist)
    for i in range(len(viewlist)):
        viewlist[i] = viewlist[i].tolist()
    print("array split into ", shardcount, "shards:")
    print(viewlist)
    print("\n\n\n")
    return viewlist

class addToShard(Resource):
    def put(self):
        global shard_id
        global shard_members
        parser = reqparse.RequestParser(bundle_errors=True)
        parser.add_argument("shard-id", type=str)
        parser.add_argument("socket-address", type=str)
        args = parser.parse_args()
        print(args, flush=True)
        if args['shard-id'] == None or args['socket-address'] == None:
            return "failed to add to shard", 500
        else:
            shard_id = args['shard-id']
            targetaddress = args['socket-address']
            members = req.get("http://"+targetaddress+"/getmembers", timeout=2)
            for member in members.json():
                shard_members[member] = members.json()[member]

            print("Nodes shard info: ", shard_members, flush=True)
            return "added to shard", 200

class getMembers(Resource):
    def get(self):
        return shard_members

if __name__ == "__main__":
    print("Hello there! CSE_Assignment4 replica instance initiated...")
    # Main instance.
    print(" Starting replica instance...")
    ip_add = "0.0.0.0"
    shardcnt = None
    #to ensure that the thread running has the required environment variables
    if 'SOCKET_ADDRESS' in os.environ and os.environ['SOCKET_ADDRESS'] != "":
        socketaddr = os.environ['SOCKET_ADDRESS']
        print("SOCKET_ADDRESS Docker env variable found! read: ", socketaddr)
    else:
        print("[ERROR] SOCKET_ADDRESS Docker env variable not found!")
    print("socket addr: ", socketaddr, flush=True)
    if 'VIEW' in os.environ and os.environ['VIEW'] != "":
        view = os.environ['VIEW']
        viewstore = view.split(",")
        viewstore.sort(key = sort_key)
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

    nodes = None
    if shardcnt != None:
        #Get node index from view list
        nodeidx = viewstore.index(socketaddr)
        #split the nodes in the viewlist into shards of index 0 to n
        nodes = assignShards(viewstore,shardcnt)
        #cannot partition nodes into shards if it cannot have at least 2 nodes per shard
        if nodes == -1:
            print("Error stated above")
        else:
            #range starts from 0
            for shard in range(int(shardcnt)):
                if socketaddr in nodes[shard]:
                    shard_id = shard
    clock = []
    for addy in viewstore:
        clock.append("0")

    causalmetadata = " ".join(clock)
    # Main instance.
    print(" Starting replica instance...")
    ip_add = "0.0.0.0"

    hr = HashRing(hash_fn=m3h)

    #add the shards to the hashring
    if shardcnt != None:
        for i in range(int(shardcnt)):
            hr.add_node("shard"+str(i))
            shards.append("shard"+str(i))
    #assign all addresses to a shard
    #if socketaddr is part of the shard, then assign shardid to the node
    if nodes != None and shardcnt != None:
        for i in range(int(shardcnt)):
            shard_members["shard"+str(i)] = nodes[i]
            if socketaddr in shard_members["shard"+str(i)]:
                shard_id = "shard"+str(i)



    print("shards created are", getShards())
    instantiateReplica(viewstore, socketaddr)
    print("instatiating..")


    api.add_resource(Store, "/key-value-store/<key>")
    api.add_resource(ViewStore, "/key-value-store-view")
    api.add_resource(ShardIDs, "/key-value-store-shard/shard-ids")
    api.add_resource(NodeShardId, "/key-value-store-shard/node-shard-id")
    api.add_resource(ShardIdMembers, "/key-value-store-shard/shard-id-members/<id>")
    api.add_resource(ShardKeyCount, "/key-value-store-shard/shard-id-key-count/<id>")
    api.add_resource(ShardAddMember, "/key-value-store-shard/add-member/<id>")
    api.add_resource(ShardReshard, "/key-value-store-shard/reshard")
    # Aux resources
    api.add_resource(NodeShardMembers, "/key-value-store-shard/node-shard-members")
    api.add_resource(KVS, "/kvs")
    api.add_resource(KVSOverwite, "/kvs/overwrite")
    api.add_resource(NodeSetShardId, "/key-value-store-shard/node-set-shard-id")
    api.add_resource(addToShard, "/addtoshard")
    api.add_resource(getMembers, "/getmembers")
    app.run(host=ip_add,port=8085)