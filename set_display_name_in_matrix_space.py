import argparse
import asyncio
import urllib.parse
from typing import Any, Dict

import aiohttp
from attrs import define
from tqdm import tqdm


@define
class HttpError(Exception):
    """Raised if a request didn't result in an HTTP 200 OK response."""
    code: int
    content: str
    url: str


class Client:
    def __init__(self, user_id: str, tok: str) -> None:
        self.user_id = user_id
        self.tok = tok

        self.base_url = None
        self.joined_rooms = []
        self.rooms_in_space = {}

    async def init(self) -> None:
        """Some async operations that can't be done in synchronous __init__, such as well-known resolution or populating
        the list of joined rooms.
        """
        split = self.user_id.split(":")
        if len(split) != 2:
            raise RuntimeError("Invalid user ID, please use the full identifier in the format: @user:domain.com")

        domain = split[1]
        try:
            resp = await self._req("GET", f"https://{domain}/.well-known/matrix/client")
            self.base_url = resp["m.homeserver"]["base_url"]
        except HttpError:
            self.base_url = f"https://{domain}"

        resp = await self._req("GET", "/_matrix/client/v3/joined_rooms")
        self.joined_rooms = resp["joined_rooms"]

    async def find_rooms_in_space(self, space: str) -> None:
        """Populate the joined rooms that are related to the given space. These are the rooms shown by /hierarchy, as
        well as DM rooms with members of the space.

        If an alias is provided for the space, it is translated as a room ID.

        Args:
            space: The ID or alias of the space to inspect.
        """
        space_id = space
        if space.startswith("#"):
            print("Resolving space...")
            resp = await self._req("GET", f"/_matrix/client/v3/directory/room/{urllib.parse.quote(space)}")
            space_id = resp["room_id"]

        print("Retrieving space hierarchy...")
        resp = await self._req("GET", f"/_matrix/client/v1/rooms/{space_id}/hierarchy")
        self.rooms_in_space = {
            room["room_id"]: room["name"]
            for room in resp["rooms"]
            if room["room_id"] in self.joined_rooms
        }

        await self._dm_rooms_in_space(space_id)

    async def _dm_rooms_in_space(self, space_id: str):
        """Find all joined rooms that are DMs with members of the given space.

        Args:
            space_id: The room ID of the space.
        """
        def _is_dm_in_space(joined_users: Dict[str, Dict]) -> bool:
            """Returns true if the room is a DM with a user in the space, i.e. it only has 2 joined users which are us
            and a member of the space.

            Args:
                joined_users: The joined users in the room, in the format returned by /joined_members.
            """
            if len(joined_users) != 2:
                return False

            for user in joined_users.keys():
                if not user == self.user_id and user not in members.keys():
                    return False

            return True

        def _correspondant_display_name(joined_users: Dict[str, Dict]) -> str:
            """Fetch the name of the correspondant in a DM room. This is very hacky and could be done much better but
            does the job well for a crappy hacky script.


            Args:
                joined_users: The joined users in the room, in the format returned by /joined_members.
            """
            for key, value in joined_users.items():
                if key != self.user_id:
                    return value["display_name"]

        print("Identifying related DMs...")

        resp = await self._req("GET", f"/_matrix/client/v3/rooms/{space_id}/joined_members")
        members = {
            key: value.get("display_name", key)
            for key, value in resp["joined"].items()
        }

        # Use a progress bar to indicate progress in inspecting each room.
        with tqdm(total=len(self.joined_rooms), unit=" rooms") as pbar:
            for room_id in self.joined_rooms:
                if room_id in self.rooms_in_space.keys():
                    pbar.update(1)
                    continue

                resp = await self._req("GET", f"/_matrix/client/v3/rooms/{room_id}/joined_members")
                joined = resp["joined"]

                if _is_dm_in_space(joined):
                    self.rooms_in_space[room_id] = f"DM with {_correspondant_display_name(joined)}"

                pbar.update(1)

    def list_target_rooms(self) -> None:
        """Pretty-print the rooms that have been identified as being related to the space."""
        for room_id, room_name in self.rooms_in_space.items():
            print(f"- \033[1m{room_name}\033[0m ({room_id})")

    async def change_display_name(self, new_display_name: str) -> None:
        """Set our display name to the given value in the rooms that have been previously identified.

        If an error occurs during one update, log it and continue to the next one.

        Args:
            new_display_name: The display name to set.
        """
        current_profile = await self._req("GET", f"/_matrix/client/v3/profile/{self.user_id}")
        evt_content = {
            "membership": "join",
            "displayname": new_display_name,
            "avatar_url": current_profile.get("avatar_url"),
        }

        # Use a progress bar to indicate progress in updating each room.
        with tqdm(total=len(self.rooms_in_space), unit=" rooms") as tbar:
            for room_id, room_name in self.rooms_in_space.items():
                try:
                    await self._req(
                        "PUT",
                        f"/_matrix/client/v3/rooms/{room_id}/state/m.room.member/{self.user_id}",
                        json=evt_content,
                    )
                except HttpError as e:
                    print(f"Failed to update \033[1m{room_name}\033[0m ({room_id}) (trying {e.url}): {e.code} - {e.content}")

                tbar.update(1)

    async def _req(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        """Send a request to the given path with the given method and arguments.

        Args:
            method: The HTTP method to use.
            path: The path to affix to the base URL. Alternatively, this can be a full HTTPS URL.
            **kwargs: The arguments to pass to aiohttp.ClientSession.request.

        Returns:
             The response parsed as JSON.

        Raises:
            RuntimeError if the path is not a full URL but no base URL is set.
            HttpError if the response's status code isn't 200.
        """
        if path.startswith("https://"):
            url = path
        else:
            if self.base_url is None:
                raise RuntimeError("Tried to make API requests before successful init")

            url = f"{self.base_url}{path}"

        async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {self.tok}"}) as session:
            async with session.request(method, url, **kwargs) as resp:
                if resp.status != 200:
                    raise HttpError(code=resp.status, content=await resp.text(), url=url)

                return await resp.json()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Helper script to scope a display name change on Matrix to a specific space."
    )
    parser.add_argument(
        "-u", "--user-id",
        required=True,
        help="The full user ID (@user:example.com) of the account on which to perform the change."
    )
    parser.add_argument(
        "-t", "--access-token",
        required=True,
        help="The access token to use to perform actions on behalf of this account."
    )
    parser.add_argument(
        "-s", "--space",
        required=True,
        help="The alias or ID of the space to scope the change to."
    )
    parser.add_argument(
        "-n", "--new-display-name",
        required=True,
        help="The new display name to set."
    )
    args = parser.parse_args()

    async def _main():
        client = Client(user_id=args.user_id, tok=args.access_token)
        await client.init()

        await client.find_rooms_in_space(args.space)
        print("\nYour display name will be changed in the following rooms:")
        client.list_target_rooms()

        choice = input("\nContinue? [Y/n] ")
        if choice == "" or choice.lower() == "y":
            await client.change_display_name(args.new_display_name)

    asyncio.run(_main())
