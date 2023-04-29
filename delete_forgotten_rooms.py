import argparse
import time
from pprint import pprint

import requests

# Define and parse command line arguments.
parser = argparse.ArgumentParser(
    description="Helper script for Synapse to delete rooms with no local user"
)

parser.add_argument(
    "-t", "--access-token",
    help="The admin access token to use to interact with the homeserver"
)
parser.add_argument(
    "-b", "--base-url",
    help="The base URL (e.g. http://localhost:8008) to use to interact with the homeserver"
)
parser.add_argument(
    "-s", "--batch-size",
    help="The amount of rooms in a batch",
    type=int,
    default=100,
)

args = parser.parse_args()

# Constants reused throughout the script.
ADMIN_BASE_URL = f"{args.base_url}/_synapse/admin"
BATCH_SIZE = args.batch_size

# Use a requests.Session to set the Authorization header so we don't have to do it again
# for each request.
s = requests.Session()
s.headers["Authorization"] = f"Bearer {args.access_token}"

# Keep iterating as long as the number of deleted rooms does not match the batch size.
# When we request the list of rooms, we request that they're ordered in increasing number
# of joined local members. Therefore, we know that if we start ignoring rooms because
# they've got at least one joined local member, then we've processed all the rooms we want
# to process.
deleted_rooms = BATCH_SIZE
while deleted_rooms == BATCH_SIZE:
    # Get the next batch of rooms to process.
    res = s.get(
        f"{ADMIN_BASE_URL}/v1/rooms?limit={BATCH_SIZE}&order_by=joined_local_members&dir=b"
    )
    if not res.ok:
        # If the request failed, we want to consider this as fatal, because there's no use
        # hammering the same endpoint and there isn't much we can do besides that.
        print("Failed to retrieve more rooms, details:")
        pprint(res.json())
        exit(1)

    rooms = res.json()["rooms"]

    print(f"Retrieved {len(rooms)} more rooms")
    deleted_rooms = 0

    # Delete each room in turn, synchronously.
    for room in rooms:
        room_id = room['room_id']

        # Skip rooms that have local members joined to them.
        if room["joined_local_members"] != 0:
            print(f"Room {room_id} has local users, skipping")
            continue

        print(f"Deleting room {room_id}")

        # Increment the counter here, so we don't have to do it twice (since we also need
        # to do it if we fail the DELETE request).
        deleted_rooms += 1

        # Send a deletion request for the room.
        res = s.delete(f"{ADMIN_BASE_URL}/v2/rooms/{room_id}", json={})
        if not res.ok:
            # If the deletion request failed, just skip the iteration. We don't want to
            # fail the entire script in this case, because the error may not be fatal. For
            # example, this might fail if there's already a purge process happening for
            # this room, which might be the case if a previous run of this script has
            # been interrupted and the script has then been run very shortly after this
            # interruption.
            print(f"Failed to request deletion for room {room_id}, skipping. Details:")
            pprint(res.json())
            continue

        # Retrieve the deletion ID for the room.
        del_id = res.json()["delete_id"]

        status = "purging"
        last_res_json = {}
        # Watch the deletion status for the room.
        # While the endpoint itself is asynchronous, we're currently using it
        # synchronously. This is because we're dealing with a large amount of rooms, with
        # a number of those likely to hold a lot of historical data which can be costly
        # to delete, so we don't want to overload the homeserver by sending a request for
        # every single room.
        # We could start deleting several rooms at once and limit the number of concurrent
        # deletions, but that's more than I am willing to implement in a quick one time
        # use script at 1AM on a Sunday morning.
        while status == "purging":
            # Sleep between requests so we don't flood the server with those. It would
            # probably be fine if we didn't do that, however it makes Synapse logs fairly
            # annoying to read.
            time.sleep(1)
            res = s.get(f"{ADMIN_BASE_URL}/v2/rooms/delete_status/{del_id}")
            # In case of a failure, simply retry.
            if not res.ok:
                print(
                    f"Failed to retrieve deletion status for room {room_id} (delete_id:"
                    f" {del_id}), details:"
                )
                pprint(res.json())

            last_res_json = res.json()
            status = last_res_json["status"]

        # If a room has failed deletion (on the Synapse side), log the full JSON payload
        # so we have access to actually helpful data.
        if status == "failed":
            print(f"Failed to delete room {room_id}, details:")
            pprint(last_res_json)

    print(f"Deleted {deleted_rooms}/{len(rooms)} rooms")
