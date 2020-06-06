docker container rm -f $(docker ps -aq)
docker build -t assignment4-img .
echo "removed containers, recompiled image, run replica 1"
clear
docker run -p 8082:8085 --net=mynet --ip=10.10.0.2 --name="node1" -e SOCKET_ADDRESS="10.10.0.2:8085" -e VIEW="10.10.0.2:8085,10.10.0.3:8085,10.10.0.4:8085,10.10.0.5:8085" -e SHARD_COUNT="2" assignment4-img
