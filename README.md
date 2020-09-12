# Distributed Key Value Store REST API 

This project is a sharded, fault tolerant key value store REST API. With this code, you may partion data onto different shards (at least 2 minimum) and request data using HTTP GET methods. In the case of replica faulure, we created a dynamic resharding mechanism which will repartion the data onto the reminaing shards to ensure the data is maintained. 

To maintain fault tolerance, we chose vector clocks as our mechanism for maintaining causual consitancy
## Dependancies
You must run docker to create the replica instances. Please visit the link below to learn how to install docker
https://www.docker.com/get-started

## Setup and usage
First, you must run the subnet command to map the replicas to an IP on the network. replica ID's are of this form: 10.10.0.2, 10.10.0.3, etc
All replicas are exposed on port 8085. To create a subnet called mynet please run the command below in temrinal:
`docker network create --subnet=10.10.0.0/16 mynet`

Next, you must compile the docker image. Please run the following command:
docker build -t assignment4.img

Finally, you may run each replica instance. We have compiled this code into an easy shell script:
`./replica1
./replica2 `

The server will run on localhost. You may now send GET,PUT or DELETE requests to any of the replicas. The replicas will note the change and broadcast to the other replicas. 

