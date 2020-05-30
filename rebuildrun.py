import unittest
import subprocess
import requests
import sys
import random
import time
import os

hostname = 'localhost'  # Windows and Mac users can change this to the docker vm ip
portNumber = '8085'
baseUrl = 'http://' + hostname + ":" + portNumber

subnetName = "assignment3-net"
subnetAddress = "10.10.0.0/16"

Instance1Name = "replica1"
Instance2Name = "replica2"
Instance3Name = "replica3"

ipAddressMainInstance = "10.10.0.2"
hostPortMainInstance = "8086"

ipAddressForwarding1Instance = "10.10.0.3"
hostPortForwarding1Instance = "8087"

ipAddressForwarding2Instance = "10.10.0.4"
hostPortForwarding2Instance = "8088"

rep1Socket = ipAddressMainInstance + ":" +portNumber
rep2Socket = ipAddressForwarding1Instance + ":" +portNumber
rep3Socket = ipAddressForwarding2Instance + ":" +portNumber

viewAddresses = "10.10.0.2:8085,10.10.0.3:8085,10.10.0.4:8085,10.10.0.5:8085,10.10.0.6:8085,10.10.0.7:8085"
shard_count = "2"

firstNodeIP = "10.10.0."
firstNodePort = "666"
instanceNumber = 8
instanceIPs = []
instanceNames = []
instanceSockets = []
instancePorts = []
for i in range(2,instanceNumber):
    instancePorts.append(firstNodePort+str(i)+":8085")
    instanceIPs.append(firstNodeIP+str(i))
    instanceNames.append("node"+str(i))
    instanceSockets.append(firstNodeIP+str(i)+":"+"8085")


### Docker Linux commands

def removeSubnet(subnetName):
    command = "docker network rm " + subnetName
    os.system(command)


def createSubnet(subnetAddress, subnetName):
    command = "docker network create --subnet=" + subnetAddress + " " + subnetName
    os.system(command)


def buildDockerImage():
    command = "docker build -t assignment3-img ."
    os.system(command)


def runInstance(hostPortNumber, ipAddress, subnetName, instanceName, socketaddress, viewAddresses, shard_count):
    command = "docker run -d -p " + hostPortNumber + " --net=" + subnetName + " --ip=" + ipAddress + " --name=" + instanceName + " -e SOCKET_ADDRESS=" + socketaddress + " -e VIEW=" + viewAddresses + " -e SHARD_COUNT=" + shard_count + " assignment3-img"
    print(command)
    os.system(command)



def stopAndRemoveInstance(instanceName):
    stopCommand = "docker stop " + instanceName
    removeCommand = "docker rm " + instanceName
    os.system(stopCommand)
    os.system(removeCommand)


def cleanUp(subnetName):
    for name in instanceNames:
        stopAndRemoveInstance(name)
    removeSubnet(subnetName)


class RunDaProgram():
    print("Clean up previous run")
    cleanUp(subnetName)
    buildDockerImage()
    createSubnet(subnetAddress, subnetName)
    for i in range(len(instancePorts)):
        runInstance(instancePorts[i], instanceIPs[i], subnetName, instanceNames[i], instanceSockets[i], viewAddresses, shard_count)


