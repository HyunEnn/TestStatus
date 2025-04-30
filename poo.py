import discord
import requests
import asyncio
import os
import urllib.parse
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
RIOT_API_KEY = os.getenv("RIOT_API_KEY")

REGION = "kr"
REGION_ROUTING = "asia"

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

monitoring_list = set()  # 3연패 이상 감시 리스트
real_time_monitoring_list = set()  # 실시간 게임 감시 리스트
monitoring_channel = None

TIER_ICON_URL = "https://raw.communitydragon.org/15.2/plugins/rcp-fe-lol-static-assets/global/default/images/ranked-emblem/emblem-{tier}.png"

async def fetch_summoner_info(game_name, tag_line):
    headers = {'X-Riot-Token': RIOT_API_KEY}
    game_name_encoded = urllib.parse.quote(game_name)
    tag_line_encoded = urllib.parse.quote(tag_line)
    account_url = f'https://{REGION_ROUTING}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name_encoded}/{tag_line_encoded}'
    account_res = requests.get(account_url, headers=headers)
    if account_res.status_code != 200:
        return None
    account_data = account_res.json()
    puuid = account_data.get('puuid')

    summoner_url = f'https://{REGION}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}'
    summoner_res = requests.get(summoner_url, headers=headers)
    if summoner_res.status_code != 200:
        return None
    summoner_data = summoner_res.json()

    return {
        'puuid': puuid,
        'summoner_id': summoner_data.get('id'),
        'profile_icon_id': summoner_data.get('profileIconId'),
        'name': summoner_data.get('name')
    }

async def fetch_rank_info(summoner_id):
    headers = {'X-Riot-Token': RIOT_API_KEY}
    league_url = f'https://{REGION}.api.riotgames.com/lol/league/v4/entries/by-summoner/{summoner_id}'
    league_res = requests.get(league_url, headers=headers)
    if league_res.status_code != 200:
        return None
    ranks = league_res.json()
    solo_rank = next((r for r in ranks if r['queueType'] == 'RANKED_SOLO_5x5'), None)
    return solo_rank

async def create_rank_embed(game_name, tag_line, summoner_info, rank_info):
    tier = rank_info['tier'] if rank_info else 'Unranked'
    rank = rank_info['rank'] if rank_info else '-'
    lp = rank_info['leaguePoints'] if rank_info else 0
    wins = rank_info['wins'] if rank_info else 0
    losses = rank_info['losses'] if rank_info else 0
    winrate = round(wins / (wins + losses) * 100, 2) if (wins + losses) > 0 else 0

    embed = discord.Embed(
        title=f"{game_name}#{tag_line} 전적",
        color=discord.Color.blue()
    )
    if summoner_info['profile_icon_id']:
        profile_icon_url = f"http://ddragon.leagueoflegends.com/cdn/14.8.1/img/profileicon/{summoner_info['profile_icon_id']}.png"
        embed.set_author(name="LoL 전적 검색", icon_url=profile_icon_url)

    if tier != 'Unranked':
        tier_icon_url = TIER_ICON_URL.format(tier=tier.lower())
        embed.set_thumbnail(url=tier_icon_url)

    embed.add_field(name="티어", value=f"{tier} {rank}", inline=True)
    embed.add_field(name="LP", value=f"{lp} LP", inline=True)
    embed.add_field(name="승/패", value=f"{wins}승 {losses}패", inline=True)
    embed.add_field(name="승률", value=f"{winrate}%", inline=True)
    embed.add_field(
        name="상세 전적",
        value=f"[lol.ps](https://lol.ps/summoner/{game_name}_{tag_line}?region=kr)",
        inline=False
    )

    return embed

async def check_in_game_status(puuid):
    headers = {'X-Riot-Token': RIOT_API_KEY}
    url = f'https://{REGION}.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}'
    response = requests.get(url, headers=headers)

    print(f"[SpectatorV5] PUUID={puuid}, Status={response.status_code}")
    return response.status_code == 200

async def monitoring_task():
    await bot.wait_until_ready()
    last_alert = {}

    while not bot.is_closed():
        if monitoring_channel is None or not monitoring_list:
            await asyncio.sleep(900)
            continue

        for riot_id in monitoring_list:
            game_name, tag_line = riot_id.split('#')
            summoner_info = await fetch_summoner_info(game_name, tag_line)
            if not summoner_info:
                continue

            puuid = summoner_info['puuid']
            headers = {'X-Riot-Token': RIOT_API_KEY}
            match_url = f'https://{REGION_ROUTING}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count=10'
            match_res = requests.get(match_url, headers=headers)
            if match_res.status_code != 200:
                continue
            match_ids = match_res.json()

            lose_streak = 0
            for match_id in match_ids:
                match_detail_url = f'https://{REGION_ROUTING}.api.riotgames.com/lol/match/v5/matches/{match_id}'
                match_detail_res = requests.get(match_detail_url, headers=headers)
                if match_detail_res.status_code != 200:
                    continue
                match_detail = match_detail_res.json()
                participants = match_detail['info']['participants']
                player = next((p for p in participants if p['puuid'] == puuid), None)
                if player:
                    if player['win']:
                        break
                    else:
                        lose_streak += 1

            if lose_streak >= 3 and last_alert.get(riot_id) != lose_streak:
                embed = discord.Embed(
                    title=f"{game_name}#{tag_line} {lose_streak}연패 중!",
                    color=discord.Color.red()
                )
                await monitoring_channel.send(embed=embed)
                last_alert[riot_id] = lose_streak

        await asyncio.sleep(900)

async def real_time_monitoring_task():
    await bot.wait_until_ready()
    last_in_game_status = {}

    while not bot.is_closed():
        if monitoring_channel is None or not real_time_monitoring_list:
            await asyncio.sleep(60)
            continue

        for riot_id in real_time_monitoring_list:
            game_name, tag_line = riot_id.split('#')
            summoner_info = await fetch_summoner_info(game_name, tag_line)
            if not summoner_info:
                print(f"[실시간감시] {riot_id} 정보 없음")
                continue

            puuid = summoner_info['puuid']
            in_game = await check_in_game_status(puuid)

            print(f"[실시간감시] {riot_id} - {'게임중' if in_game else '게임 안함'}")

            if in_game and last_in_game_status.get(riot_id) != "in_game":
                embed = discord.Embed(
                    title=f"{game_name}#{tag_line} 게임 시작!",
                    description="🕹️ 현재 게임이 진행 중이에요!",
                    color=discord.Color.gold()
                )
                await monitoring_channel.send(embed=embed)
                last_in_game_status[riot_id] = "in_game"

            elif not in_game:
                last_in_game_status[riot_id] = "idle"

        await asyncio.sleep(60)

async def fetch_current_game_info(puuid):
    headers = {'X-Riot-Token': RIOT_API_KEY}
    url = f'https://{REGION}.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}'
    res = requests.get(url, headers=headers)

    print(f"[InGameInfo] PUUID={puuid}, Status={res.status_code}")
    if res.status_code != 200:
        return None

    return res.json()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    bot.loop.create_task(monitoring_task())
    bot.loop.create_task(real_time_monitoring_task())

@bot.event
async def on_message(message):
    global monitoring_channel

    if message.author == bot.user:
        return

    if message.content.startswith('!전적 '):
        riot_id = message.content[4:].strip()
        if '#' not in riot_id:
            await message.channel.send('올바른 형식: 이름#태그')
            return

        game_name, tag_line = riot_id.split('#', 1)
        await message.channel.send(f"[{game_name}#{tag_line}] 전적을 불러오는 중...")

        summoner_info = await fetch_summoner_info(game_name, tag_line)
        if not summoner_info:
            await message.channel.send('소환사 정보를 찾을 수 없어.')
            return

        rank_info = await fetch_rank_info(summoner_info['summoner_id'])
        embed = await create_rank_embed(game_name, tag_line, summoner_info, rank_info)
        await message.channel.send(embed=embed)

    elif message.content.startswith('!모니터링추가 '):
        parts = message.content.split(' ', 1)
        if len(parts) < 2:
            await message.channel.send('올바른 형식: 이름#태그')
            return

        riot_id = parts[1].strip()
        if(riot_id in monitoring_list):
            await message.channel.send(f"❌ `{riot_id}` 은 이미 리스트에 있어")
            return
    

        # 소환사 전적 Embed 바로 출력
        game_name, tag_line = riot_id.split('#', 1)
        summoner_info = await fetch_summoner_info(game_name, tag_line)
        if not summoner_info:
            await message.channel.send('소환사 정보를 찾을 수 없어.')
            return
        
        monitoring_list.add(riot_id)
        monitoring_channel = message.channel
        await message.channel.send(f"✅ `{riot_id}` 모니터링 리스트에 추가했어!")
    
        rank_info = await fetch_rank_info(summoner_info['summoner_id'])
        embed = await create_rank_embed(game_name, tag_line, summoner_info, rank_info)
        await message.channel.send(embed=embed)

        # 추가한 직후 바로 연패 상태도 체크
        puuid = summoner_info['puuid']
        headers = {'X-Riot-Token': RIOT_API_KEY}
        match_url = f'https://{REGION_ROUTING}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count=10'
        match_res = requests.get(match_url, headers=headers)
        if match_res.status_code == 200:
            match_ids = match_res.json()

            lose_streak = 0
            for match_id in match_ids:
                match_detail_url = f'https://{REGION_ROUTING}.api.riotgames.com/lol/match/v5/matches/{match_id}'
                match_detail_res = requests.get(match_detail_url, headers=headers)
                if match_detail_res.status_code != 200:
                    continue
                match_detail = match_detail_res.json()
                participants = match_detail['info']['participants']
                player = next((p for p in participants if p['puuid'] == puuid), None)
                if player:
                    if player['win']:
                        break
                    else:
                        lose_streak += 1

            if lose_streak >= 3:
                embed = discord.Embed(
                    title=f"{game_name}#{tag_line} {lose_streak}연패 중!",
                    color=discord.Color.red()
                )
                await monitoring_channel.send(embed=embed)

    elif message.content.startswith('!모니터링삭제 '):
        parts = message.content.split(' ', 1)
        if len(parts) < 2:
            await message.channel.send('올바른 형식: 이름#태그')
            return

        riot_id = parts[1].strip()
        if riot_id in monitoring_list:
            monitoring_list.remove(riot_id)
            await message.channel.send(f"✅ `{riot_id}` 모니터링 리스트에서 삭제했어!")
        else:
            await message.channel.send(f"❌ `{riot_id}` 은 모니터링 리스트에 없어.")

    elif message.content.startswith('!모니터링리스트'):
        if not monitoring_list:
            await message.channel.send('현재 모니터링하는 소환사가 없어.')
        else:
            list_text = "\n".join(monitoring_list)
            await message.channel.send(f"📋 현재 모니터링 리스트:\n{list_text}")

    elif message.content.startswith('!실시간추가'):
        parts = message.content.split(' ', 1)
        if len(parts) < 2:
            await message.channel.send('❗ 올바른 형식: 이름#태그')
            return

        riot_id = parts[1].strip()

        # 이미 리스트에 있는지 확인
        if riot_id in real_time_monitoring_list:
            await message.channel.send(f"❌ `{riot_id}` 은 이미 실시간 감시 리스트에 있어.")
            return

        # 소환사 정보 유효성 확인
        game_name, tag_line = riot_id.split('#', 1)
        summoner_info = await fetch_summoner_info(game_name, tag_line)
        if not summoner_info:
            await message.channel.send(f"❌ `{riot_id}` 소환사 정보를 찾을 수 없어. 감시 리스트에 추가되지 않았어.")
            return

        # 정상적으로 추가
        real_time_monitoring_list.add(riot_id)
        monitoring_channel = message.channel
        await message.channel.send(f"✅ `{riot_id}` 실시간 감시 리스트에 추가했어!")

        # 전적 임베드 출력
        rank_info = await fetch_rank_info(summoner_info['summoner_id'])
        embed = await create_rank_embed(game_name, tag_line, summoner_info, rank_info)
        await message.channel.send(embed=embed)

    elif message.content.startswith('!실시간삭제 '):
        parts = message.content.split(' ', 1)
        if len(parts) < 2:
            await message.channel.send('올바른 형식: 이름#태그')
            return

        riot_id = parts[1].strip()
        if riot_id in real_time_monitoring_list:
            real_time_monitoring_list.remove(riot_id)
            await message.channel.send(f"✅ `{riot_id}` 실시간 감시 리스트에서 삭제했어!")
        else:
            await message.channel.send(f"❌ `{riot_id}` 은 실시간 감시 리스트에 없어.")

    elif message.content.startswith('!실시간리스트'):
        if not real_time_monitoring_list:
            await message.channel.send('현재 실시간 감시하는 소환사가 없어.')
        else:
            list_text = "\n".join(real_time_monitoring_list)
            await message.channel.send(f"📋 현재 실시간 감시 리스트:\n{list_text}")
            
    elif message.content.startswith('!푸바오'):
        riot_id = '강해린#왕자님'
        game_name, tag_line = riot_id.split('#', 1)
        await message.channel.send(f"[{game_name}#{tag_line}] 전적을 불러오는 중...")

        summoner_info = await fetch_summoner_info(game_name, tag_line)
        if not summoner_info:
            await message.channel.send('소환사 정보를 찾을 수 없어.')
            return

        rank_info = await fetch_rank_info(summoner_info['summoner_id'])
        embed = await create_rank_embed(game_name, tag_line, summoner_info, rank_info)
        await message.channel.send(embed=embed)

    elif message.content.startswith('!인게임정보'):
        parts = message.content.split(' ', 1)
        if len(parts) < 2 or '#' not in parts[1]:
            await message.channel.send("형식: `!인게임정보 이름#태그`")
            return

        riot_id = parts[1].strip()
        game_name, tag_line = riot_id.split('#', 1)
        summoner_info = await fetch_summoner_info(game_name, tag_line)
        if not summoner_info:
            await message.channel.send("소환사 정보를 찾을 수 없어.")
            return

        game_data = await fetch_current_game_info(summoner_info['puuid'])
        if not game_data:
            await message.channel.send(f"{game_name}#{tag_line}님은 현재 게임 중이 아니야.")
            return

        game_mode = game_data.get('gameMode', '알 수 없음')
        game_start = int(game_data['gameStartTime'] / 1000)
        embed = discord.Embed(
            title=f"{game_name}#{tag_line} 현재 게임 정보",
            description=f"게임 모드: {game_mode}\n시작 시간: <t:{game_start}:R>",
            color=discord.Color.green()
        )

        blue_team = [p['summonerName'] for p in game_data['participants'] if p['teamId'] == 100]
        red_team = [p['summonerName'] for p in game_data['participants'] if p['teamId'] == 200]
        embed.add_field(name="🟦 블루팀", value="\n".join(blue_team), inline=True)
        embed.add_field(name="🟥 레드팀", value="\n".join(red_team), inline=True)

        await message.channel.send(embed=embed)

    elif message.content == '/help':
        embed = discord.Embed(
            title="🛠️ 사용 가능한 명령어 목록",
            description="명령어를 입력해서 기능을 사용할 수 있습니다!",
            color=discord.Color.purple()
        )
        embed.add_field(name="!전적 [이름#태그]", value="소환사 전적 조회", inline=False)
        embed.add_field(name="!모니터링추가 [이름#태그]", value="3연패 이상 감시 추가", inline=False)
        embed.add_field(name="!모니터링삭제 [이름#태그]", value="연패 감시 삭제", inline=False)
        embed.add_field(name="!모니터링리스트", value="연패 감시 리스트 조회", inline=False)
        embed.add_field(name="!실시간추가 [이름#태그]", value="실시간 게임 감시 추가", inline=False)
        embed.add_field(name="!실시간삭제 [이름#태그]", value="실시간 감시 삭제", inline=False)
        embed.add_field(name="!실시간리스트", value="실시간 감시 리스트 조회", inline=False)
        embed.add_field(name="!푸바오", value="강해린#왕자님 전적 조회", inline=False)
        embed.add_field(name="!인게임정보 [이름#태그]", Value="인게임 정보 조회", inline=False)
        embed.add_field(name="/help", value="명령어 설명 보기", inline=False)
        
        await message.channel.send(embed=embed)

bot.run(DISCORD_TOKEN)
