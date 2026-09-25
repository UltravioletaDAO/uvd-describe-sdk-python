"""ENSIP-12: the `avatar` text record, turned into a URL a browser can load.

uvdweb did this with ethers' `getAvatar` (`WalletConnect.js:143-159`), which is
the behaviour mirrored here, NFT ownership check included:

    https://… / http://… / data:…  → as they are
    ipfs://<cid>/<path>            → <ipfs gateway>/ipfs/<cid>/<path>
    ipns://<name>                  → <ipfs gateway>/ipns/<name>
    ar://<tx>                      → https://arweave.net/<tx>
    eip155:<chain>/erc721:<contract>/<id>   → the NFT's image, IF the name's
    eip155:<chain>/erc1155:<contract>/<id>    address owns that token

The ownership check is the part that matters: anybody can point their avatar at
a famous NFT. An NFT the name's address does not own gives no URL (the record
stays in `raw_value`, and `detail` says why).

The metadata fetch goes through `_proto.check_url` like a CCIP gateway: the
token URI is written by whoever deployed the NFT contract.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Optional, Tuple
from urllib.parse import unquote

from . import _abi
from ._hash import selector
from ._proto import Call, Fetch, FetchResponse, Reverted, Step, Unavailable, check_url

_NFT = re.compile(r"eip155:(\d+)/(erc721|erc1155):(0x[0-9a-fA-F]{40})/(\d+)", re.IGNORECASE)
_SEL_OWNER_OF = selector("ownerOf(uint256)")
_SEL_BALANCE_OF = selector("balanceOf(address,uint256)")
_SEL_TOKEN_URI = selector("tokenURI(uint256)")
_SEL_URI = selector("uri(uint256)")


class NoAvatar(Exception):
    """The record is set but yields no URL. Not an error of the name."""


def plain_url(uri: str, ipfs_gateway: str) -> Optional[str]:
    """The browser URL of a non-NFT URI, or `None` if it is not one we know."""
    uri = uri.strip()
    low = uri.lower()
    if low.startswith(("https://", "http://", "data:")):
        return uri
    if low.startswith("ipfs://"):
        rest = uri[len("ipfs://") :]
        if rest.lower().startswith("ipfs/"):
            rest = rest[len("ipfs/") :]
        return f"{ipfs_gateway.rstrip('/')}/ipfs/{rest}"
    if low.startswith("ipns://"):
        return f"{ipfs_gateway.rstrip('/')}/ipns/{uri[len('ipns://') :]}"
    if low.startswith("ar://"):
        return f"https://arweave.net/{uri[len('ar://') :]}"
    return None


def nft_reference(record: str) -> Optional[Tuple[str, str, str, int]]:
    """`(chain, standard, contract, token_id)` of an NFT avatar, or `None`."""
    match = _NFT.fullmatch(record.strip())
    if not match:
        return None
    chain_id, standard, contract, token = match.groups()
    return f"eip155:{int(chain_id)}", standard.lower(), contract, int(token)


def _metadata(uri: str, ipfs_gateway: str) -> Step[Any]:
    low = uri.lower()
    if low.startswith("data:"):
        header, _, payload = uri.partition(",")
        try:
            raw = (
                base64.b64decode(payload)
                if header.endswith(";base64")
                else unquote(payload).encode()
            )
            return json.loads(raw)
        except ValueError:
            raise NoAvatar("the NFT metadata (a data: URI) is not JSON") from None
    url = plain_url(uri, ipfs_gateway)
    if url is None or not url.lower().startswith("https://"):
        raise NoAvatar("the NFT metadata is not at an https, ipfs or ar URL")
    check_url(url)
    response: FetchResponse = yield Fetch(url)
    if not 200 <= response.status < 300:
        raise Unavailable(f"the NFT metadata answered HTTP {response.status}")
    try:
        return json.loads(response.body)
    except ValueError:
        raise NoAvatar("the NFT metadata is not JSON") from None


def nft_image(record: str, owner: str, ipfs_gateway: str) -> Step[str]:
    """The image URL of an NFT avatar that `owner` holds. Raises `NoAvatar`."""
    reference = nft_reference(record)
    if reference is None:
        raise NoAvatar("the avatar record is not an ENSIP-12 URI")
    chain, standard, contract, token = reference
    try:
        if standard == "erc721":
            out: bytes = yield Call(
                chain, contract, _SEL_OWNER_OF + _abi.encode(["uint256"], [token])
            )
            (holder,) = _abi.decode(["address"], out)
            if holder.lower() != owner.lower():
                raise NoAvatar("the NFT is not owned by the name's address")
            out = yield Call(chain, contract, _SEL_TOKEN_URI + _abi.encode(["uint256"], [token]))
        else:
            out = yield Call(
                chain,
                contract,
                _SEL_BALANCE_OF + _abi.encode(["address", "uint256"], [owner, token]),
            )
            (balance,) = _abi.decode(["uint256"], out)
            if balance == 0:
                raise NoAvatar("the NFT is not owned by the name's address")
            out = yield Call(chain, contract, _SEL_URI + _abi.encode(["uint256"], [token]))
        (token_uri,) = _abi.decode(["string"], out)
    except (Reverted, _abi.AbiError):
        raise NoAvatar("the NFT contract did not answer as an ERC-721/1155") from None
    if standard == "erc1155":
        token_uri = token_uri.replace("{id}", format(token, "064x"))
    meta = yield from _metadata(token_uri, ipfs_gateway)
    if not isinstance(meta, dict):
        raise NoAvatar("the NFT metadata is not a JSON object")
    image = meta.get("image") or meta.get("image_url")
    if not image and isinstance(meta.get("image_data"), str):
        image = "data:image/svg+xml;utf8," + meta["image_data"]
    if not isinstance(image, str):
        raise NoAvatar("the NFT metadata has no image")
    url = plain_url(image, ipfs_gateway)
    if url is None:
        raise NoAvatar("the NFT image is not a URL this SDK can load")
    return url
