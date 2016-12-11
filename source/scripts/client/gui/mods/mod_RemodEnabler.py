# -*- coding: utf-8 -*-
import binascii
import cProfile
import copy
import copy_reg
import glob
import os
import pstats
import shutil
import traceback
from collections import namedtuple
from functools import partial
from zipfile import ZipFile

import BigWorld
import Math
import ResMgr

import GUI
import Keys
import material_kinds
from AvatarInputHandler import mathUtils
from CurrentVehicle import g_currentPreviewVehicle
from Vehicle import Vehicle
from adisp import async, process
from gui import InputHandler, SystemMessages
from gui.ClientHangarSpace import _VehicleAppearance
from gui.Scaleform.Waiting import Waiting
from gui.Scaleform.daapi.view.lobby.LobbyView import LobbyView
from gui.Scaleform.daapi.view.login.LoginView import LoginView
from gui.app_loader.loader import g_appLoader
from gui.mods import PYmodsCore
from helpers import getClientVersion
from items import vehicles
from vehicle_systems import appearance_cache
from vehicle_systems.tankStructure import TankPartNames

try:
    from gui.mods import mod_PYmodsGUI
except ImportError:
    mod_PYmodsGUI = None
    print 'RemodEnabler: no-GUI mode activated'
except StandardError:
    mod_PYmodsGUI = None
    traceback.print_exc()

res = ResMgr.openSection('../paths.xml')
sb = res['Paths']
vl = sb.values()[0]
if vl is not None and not hasattr(BigWorld, 'curCV'):
    BigWorld.curCV = vl.asString


# noinspection PyDecorator,PyUnresolvedReferences
@staticmethod
def new_show(message, isSingle=False, interruptCallback=lambda: None):
    if 'Remod: ' not in message:
        return old_show(message, isSingle, interruptCallback)
    BigWorld.Screener.setEnabled(False)
    if not (isSingle and message in Waiting._Waiting__waitingStack):
        Waiting._Waiting__waitingStack.append(message)
    view = Waiting.getWaitingView()
    if view is not None:
        view.showS(message.replace('Remod: ', ''))
        Waiting._Waiting__isVisible = True
        view.setCallback(interruptCallback)
        from gui.shared.events import LobbySimpleEvent
        from gui.shared import EVENT_BUS_SCOPE
        view.fireEvent(LobbySimpleEvent(LobbySimpleEvent.WAITING_SHOWN), scope=EVENT_BUS_SCOPE.LOBBY)


old_show = Waiting.show
Waiting.show = new_show

EmblemSlot = namedtuple('EmblemSlot', ['rayStart',
                                       'rayEnd',
                                       'rayUp',
                                       'size',
                                       'hideIfDamaged',
                                       'type',
                                       'isMirrored',
                                       'isUVProportional',
                                       'emblemId'])


def readAODecals(confList):
    retVal = []
    for subDict in confList:
        m = Math.Matrix()
        for strNum, matStr in enumerate(sorted(subDict['transform'].keys())):
            for colNum, elemNum in enumerate(subDict['transform'][matStr]):
                m.setElement(strNum, colNum, elemNum)
        retVal.append(m)

    return retVal


def readEmblemSlots(confList):
    slots = []
    for confDict in confList:
        if confDict['type'] not in ('player', 'clan', 'inscription', 'insigniaOnGun'):
            print '%s: not supported emblem slot type: %s, expected: %s' % (
                _config.ID, confDict['type'], ' '.join(('player', 'clan', 'inscription', 'insigniaOnGun')))
        descr = EmblemSlot(Math.Vector3(tuple(confDict['rayStart'])), Math.Vector3(tuple(confDict['rayEnd'])),
                           Math.Vector3(tuple(confDict['rayUp'])), confDict['size'],
                           confDict.get('hideIfDamaged', False), confDict['type'], confDict.get('isMirrored', False),
                           confDict.get('isUVProportional', True), confDict.get('emblemId', None))
        slots.append(descr)

    return slots


class OM(object):
    def __init__(self):
        self.models = {}
        self.enabled = False
        self.allDesc = {'Player': [''],
                        'Ally': [''],
                        'Enemy': ['']}
        self.remodTanks = {}
        self.selected = {'Player': {},
                         'Ally': {},
                         'Enemy': {},
                         'Remod': ''}


class OMDescriptor(object):
    def __init__(self):
        self.name = ''
        self.enabled = False
        self.swapPlayer = True
        self.swapAlly = True
        self.swapEnemy = True
        self.swapAll = {'Player': True,
                        'Ally': True,
                        'Enemy': True}
        self.whitelists = {'Player': [],
                           'Ally': [],
                           'Enemy': []}


class OS(object):
    def __init__(self):
        self.models = {'static': {}, 'dynamic': {}}
        self.enabled = False
        self.priorities = {skinType: {'Player': [],
                                      'Ally': [],
                                      'Enemy': []} for skinType in self.models}


class OSDescriptor(object):
    def __init__(self):
        self.name = ''
        self.enabled = False
        self.swapPlayer = True
        self.swapAlly = True
        self.swapEnemy = True
        self.whitelist = []
        self.hasSegmentTex = {}


class _Config(PYmodsCore._Config):
    def __init__(self):
        super(_Config, self).__init__(__file__)
        self.version = '2.0.0 (%s)' % self.version
        self.author = '%s (thx to atacms)' % self.author
        self.possibleModes = ['player', 'ally', 'enemy', 'remod']
        self.defaultSkinConfig = {'enabled': True,
                                  'swapPlayer': True,
                                  'swapAlly': False,
                                  'swapEnemy': True}
        self.defaultKeys = {'ChangeViewHotKey': ['KEY_F2', ['KEY_LCONTROL', 'KEY_RCONTROL']],
                            'ChangeViewHotkey': [Keys.KEY_F2, [Keys.KEY_LCONTROL, Keys.KEY_RCONTROL]],
                            'SwitchRemodHotKey': ['KEY_F3', ['KEY_LCONTROL', 'KEY_RCONTROL']],
                            'SwitchRemodHotkey': [Keys.KEY_F3, [Keys.KEY_LCONTROL, Keys.KEY_RCONTROL]],
                            'CollisionHotKey': ['KEY_F4', ['KEY_LCONTROL', 'KEY_RCONTROL']],
                            'CollisionHotkey': [Keys.KEY_F4, [Keys.KEY_LCONTROL, Keys.KEY_RCONTROL]]}
        self.data = {'enabled': True,
                     'isDebug': True,
                     'collisionEnabled': False,
                     'collisionComparisonEnabled': False,
                     'oldConfigPrints': [],
                     'ChangeViewHotKey': self.defaultKeys['ChangeViewHotKey'],
                     'ChangeViewHotkey': self.defaultKeys['ChangeViewHotkey'],
                     'CollisionHotKey': self.defaultKeys['CollisionHotKey'],
                     'CollisionHotkey': self.defaultKeys['CollisionHotkey'],
                     'SwitchRemodHotKey': self.defaultKeys['SwitchRemodHotKey'],
                     'SwitchRemodHotkey': self.defaultKeys['SwitchRemodHotkey'],
                     'currentMode': self.possibleModes[0],
                     'remod': True}
        self.i18n = {
            'UI_description': 'Remod Enabler',
            'UI_setting_isDebug_text': 'Enable extended log printing',
            'UI_setting_isDebug_tooltip': ('{HEADER}Description:{/HEADER}{BODY}If enabled, your python.log '
                                           'will be harassed with mod\'s debug information.{/BODY}'),
            'UI_setting_remod_text': 'Enable all remods preview mode',
            'UI_setting_remod_tooltip': ('{HEADER}Description:{/HEADER}{BODY}If disabled, all remods preview '
                                         'mode will not be active.{/BODY}'),
            'UI_setting_changeView_text': 'View mode switch hotkey',
            'UI_setting_changeView_tooltip': (
                '{HEADER}Description:{/HEADER}{BODY}This hotkey will switch the preview mode in hangar.\n<b>Possible '
                'modes:</b>\n • Player tank\n • Ally tank\n • Enemy tank{remod}{/BODY}'),
            'UI_setting_changeView_remod': '\n • Remod preview',
            'UI_setting_collision_text': 'Collision view switch hotkey',
            'UI_setting_collision_tooltip': (
                '{HEADER}Description:{/HEADER}{BODY}This hotkey will switch collision preview mode in hangar.\n'
                '<b>Possible modes:</b>\n • OFF\n • Model replace\n • Model add{/BODY}'),
            'UI_setting_switchRemod_text': 'Remod switch hotkey',
            'UI_setting_switchRemod_tooltip': (
                '{HEADER}Description:{/HEADER}{BODY}This hotkey will cycle through all remods (ignoring whitelists in '
                'remod preview mode).{/BODY}'),
            'UI_setting_curRemodSwapPlayer_text': 'Enable current remod to player tanks',
            'UI_setting_curRemodSwapPlayer_tooltip': (
                '{HEADER}Description:{/HEADER}{BODY}Current remod will be able to be applied to player tanks according '
                'to its whitelist settings.{/BODY}'),
            'UI_setting_curRemodUsePlayerWhitelist_text': 'Enable player whitelist usage of current remod',
            'UI_setting_curRemodUsePlayerWhitelist_tooltip': (
                '{HEADER}Description:{/HEADER}{BODY}If disabled, this remod will be whitelisted for all player tanks.\n'
                '<b>Current whitelist:</b>\n'),
            'UI_setting_curRemodSwapAlly_text': 'Enable current remod to ally tanks',
            'UI_setting_curRemodSwapAlly_tooltip': (
                '{HEADER}Description:{/HEADER}{BODY}Current remod will be able to be applied to ally tanks according to '
                'its whitelist settings.{/BODY}'),
            'UI_setting_curRemodUseAllyWhitelist_text': 'Enable ally whitelist usage of current remod',
            'UI_setting_curRemodUseAllyWhitelist_tooltip': (
                '{HEADER}Description:{/HEADER}{BODY}If disabled, this remod will be whitelisted for all ally tanks.\n'
                '<b>Current whitelist:</b>\n'),
            'UI_setting_curRemodSwapEnemy_text': 'Enable current remod to enemy tanks',
            'UI_setting_curRemodSwapEnemy_tooltip': (
                '{HEADER}Description:{/HEADER}{BODY}Current remod will be able to be applied to enemy tanks according '
                'to its whitelist settings.{/BODY}'),
            'UI_setting_curRemodUseEnemyWhitelist_text': 'Enable enemy whitelist usage of current remod',
            'UI_setting_curRemodUseEnemyWhitelist_tooltip': (
                '{HEADER}Description:{/HEADER}{BODY}If disabled, this remod will be whitelisted for all enemy tanks.\n'
                '<b>Current whitelist:</b>\n'),
            'UI_setting_curRemodEmptyWhitelist': ' • This remod\'s whitelist is empty, so all tanks will be affected.{/BODY}',
            'UI_setting_curSkinSwapPlayer_text': 'Enable current skin to player tanks',
            'UI_setting_curSkinSwapPlayer_tooltip': (
                '{HEADER}Description:{/HEADER}{BODY}Current skin will be able to be applied to player tanks according '
                'to its whitelist.{/BODY}'),
            'UI_setting_curSkinSwapAlly_text': 'Enable current skin to ally tanks',
            'UI_setting_curSkinSwapAlly_tooltip': (
                '{HEADER}Description:{/HEADER}{BODY}Current skin will be able to be applied to ally tanks according to '
                'its whitelist.{/BODY}'),
            'UI_setting_curSkinSwapEnemy_text': 'Enable current skin to enemy tanks',
            'UI_setting_curSkinSwapEnemy_tooltip': (
                '{HEADER}Description:{/HEADER}{BODY}Current skin will be able to be applied to enemy tanks according '
                'to its whitelist.{/BODY}'),
            'UI_disableCollisionComparison': '<b>RemodEnabler:</b>\nDisabling collision comparison mode.',
            'UI_enableCollisionComparison': '<b>RemodEnabler:</b>\nEnabling collision comparison mode.',
            'UI_enableCollision': '<b>RemodEnabler:</b>\nEnabling collision mode.',
            'UI_install_skin': '<b>RemodEnabler:</b>\nSkin installed: ',
            'UI_install_remod': '<b>RemodEnabler:</b>\nRemod installed: ',
            'UI_install_default': '<b>RemodEnabler:</b>\nDefault model applied.',
            'UI_mode': '<b>RemodEnabler:</b>\nCurrent display mode: ',
            'UI_mode_player': 'player tank preview',
            'UI_mode_ally': 'ally tank preview',
            'UI_mode_enemy': 'enemy tank preview',
            'UI_mode_remod': 'all remods preview',
            'UI_loading_header_CRC32': 'RemodEnabler:\nchecking textures...',
            'UI_loading_header_models_unpack': 'RemodEnabler:\nunpacking models...'}
        self.configsDict = {}
        self.OM = OM()
        self.OS = OS()
        self.OMDesc = None
        self.OSDesc = None
        self.curVehicleName = None
        self.loadLang()

    def template_settings(self):
        template = {'modDisplayName': self.i18n['UI_description'],
                    'settingsVersion': 200,
                    'enabled': True,
                    'column1': [],
                    'column2': [{'type': 'HotKey',
                                 'text': self.i18n['UI_setting_changeView_text'],
                                 'tooltip': self.i18n['UI_setting_changeView_tooltip'].format(
                                     remod=self.i18n['UI_setting_changeView_remod'] if self.data['remod'] else '',
                                     **self.tooltipSubs),
                                 'value': self.data['ChangeViewHotkey'],
                                 'defaultValue': self.defaultKeys['ChangeViewHotkey'],
                                 'varName': 'ChangeViewHotkey'},
                                {'type': 'HotKey',
                                 'text': self.i18n['UI_setting_switchRemod_text'],
                                 'tooltip': self.i18n['UI_setting_switchRemod_tooltip'],
                                 'value': self.data['SwitchRemodHotkey'],
                                 'defaultValue': self.defaultKeys['SwitchRemodHotkey'],
                                 'varName': 'SwitchRemodHotkey'},
                                {'type': 'HotKey',
                                 'text': self.i18n['UI_setting_collision_text'],
                                 'tooltip': self.i18n['UI_setting_collision_tooltip'],
                                 'value': self.data['CollisionHotkey'],
                                 'defaultValue': self.defaultKeys['CollisionHotkey'],
                                 'varName': 'CollisionHotkey'}]}
        if self.OMDesc is not None:
            for key in ('SwapPlayer', 'Player', 'SwapAlly', 'Ally', 'SwapEnemy', 'Enemy'):
                if 'Swap' in key:
                    self.data['curRemod%s' % key] = getattr(self.OMDesc, key[0].lower() + key[1:])
                    template['column1'].append({'type': 'CheckBox',
                                                'text': self.i18n['UI_setting_curRemod%s_text' % key],
                                                'value': self.data['curRemod%s' % key],
                                                'tooltip': self.i18n['UI_setting_curRemod%s_tooltip' % key],
                                                'varName': 'curRemod%s' % key})
                else:
                    self.data['curRemodUse%sWhitelist' % key] = not self.OMDesc.swapAll[key]
                    tooltipStr = self.i18n['UI_setting_curRemodUse%sWhitelist_tooltip' % key]
                    whitelist = self.OMDesc.whitelists[key]
                    if len(whitelist):
                        tooltipStr += ' • ' + '\n • '.join(whitelist) + '{/BODY}'
                    else:
                        tooltipStr += self.i18n['UI_setting_curRemodEmptyWhitelist']
                    template['column1'].append({'type': 'CheckBox',
                                                'text': self.i18n['UI_setting_curRemodUse%sWhitelist_text' % key],
                                                'value': self.data['curRemodUse%sWhitelist' % key],
                                                'tooltip': tooltipStr,
                                                'varName': 'curRemodUse%sWhitelist' % key})
        elif self.OSDesc is not None:
            for key in ('SwapPlayer', 'SwapAlly', 'SwapEnemy'):
                self.data['curSkin%s' % key] = getattr(self.OSDesc, key[0].lower() + key[1:])
                template['column1'].append({'type': 'CheckBox',
                                            'text': self.i18n['UI_setting_curSkin%s_text' % key],
                                            'value': self.data['curSkin%s' % key],
                                            'tooltip': self.i18n['UI_setting_curSkin%s_tooltip' % key],
                                            'varName': 'curSkin%s' % key})
        else:
            template['column1'].extend(({'type': 'CheckBox',
                                         'text': self.i18n['UI_setting_isDebug_text'],
                                         'value': self.data['isDebug'],
                                         'tooltip': self.i18n['UI_setting_isDebug_tooltip'],
                                         'varName': 'isDebug'},
                                        {'type': 'CheckBox',
                                         'text': self.i18n['UI_setting_remod_text'],
                                         'value': self.data['remod'],
                                         'tooltip': self.i18n['UI_setting_remod_tooltip'],
                                         'varName': 'remod'}))
        return template

    def apply_settings(self, settings):
        super(_Config, self).apply_settings(settings)
        if getattr(self, 'OMDesc', None) is not None:
            OMDict = {}
            for key in ('SwapPlayer', 'Player', 'SwapAlly', 'Ally', 'SwapEnemy', 'Enemy'):
                if 'Swap' in key:
                    setattr(self.OMDesc, key[0].lower() + key[1:],
                            self.data.get('curRemod%s' % key, getattr(self.OMDesc, key[0].lower() + key[1:])))
                    OMDict[key[0].lower() + key[1:]] = self.data.get(
                        'curRemod%s' % key, getattr(self.OMDesc, key[0].lower() + key[1:]))
                else:
                    self.OMDesc.swapAll[key] = not self.data.get('curRemodUse%sWhitelist' % key,
                                                                 not self.OMDesc.swapAll[key])
                    OMDict['use%sWhitelist' % key] = self.data.get('curRemodUse%sWhitelist' % key,
                                                                   not self.OMDesc.swapAll[key])
            self.loadJson(self.OMDesc.name, OMDict, '%sremods/' % self.configPath, True, False)
        elif getattr(self, 'OSDesc', None) is not None:
            OSDict = {}
            for key in ('SwapPlayer', 'SwapAlly', 'SwapEnemy'):
                setattr(self.OSDesc, key[0].lower() + key[1:],
                        self.data.get('curSkin%s' % key, getattr(self.OSDesc, key[0].lower() + key[1:])))
                OSDict[key[0].lower() + key[1:]] = self.data.get(
                    'curSkin%s' % key, getattr(self.OSDesc, key[0].lower() + key[1:]))
            self.loadJson(self.OSDesc.name, OSDict, '%sskins/' % self.configPath, True, False)
        _gui_config.update_template('%s' % self.ID, self.template_settings)

    def update_settings(self, doPrint=False):
        super(_Config, self).update_settings(self.data['isDebug'])
        _gui_config.updateFile('%s' % self.ID, self.data, self.template_settings)

    def onWindowClose(self):
        g_currentPreviewVehicle.refreshModel()
        '''else:
            from ConnectionManager import connectionManager

            def new_isConnected():
                return True
            connectionManager.isConnected = new_isConnected
            g_appLoader.showLobby()'''

    def update_data(self, doPrint=False):
        super(_Config, self).update_data(doPrint)
        self.OM.enabled = os.path.isdir('%s/vehicles/remods/' % BigWorld.curCV) and len(
            glob.glob('%s/vehicles/remods/*' % BigWorld.curCV))
        if self.OM.enabled:
            self.OM.selected = self.loadJson('remodsCache', self.OM.selected, self.configPath)
            configsPath = '%sremods/*.json' % self.configPath
            for configPath in glob.iglob(configsPath):
                confPath = configPath.replace('%s/' % BigWorld.curCV, '')
                sname = os.path.basename(configPath).split('.')[0]
                self.configsDict[sname] = confDict = self.loadJson(sname, self.configsDict.get(sname, {}),
                                                                   os.path.dirname(configPath) + '/')
                if not confDict:
                    print '%s: error while reading %s.' % (self.ID, os.path.basename(confPath))
                    continue
                if 'enabled' in confDict and not confDict['enabled']:
                    print '%s: %s disabled, moving on' % (self.ID, sname)
                    continue
                self.OM.models[sname] = pRecord = OMDescriptor()
                pRecord.name = sname
                pRecord.emblemSlotsGun = None
                pRecord.emblemSlotsHull = None
                pRecord.emblemSlotsTurret = None
                pRecord.strCamoMask = pRecord.strCamoMaskHull = pRecord.strCamoMaskTurret = pRecord.strCamoMaskGun = ''
                pRecord.strCamoTiling = pRecord.strCamoTilingHull = pRecord.strCamoTilingTurret = \
                    pRecord.strCamoTilingGun = (1.0, 1.0, 0.0, 0.0)
                pRecord.strGunEffects = pRecord.strGunReloadEffect = ''
                pRecord.exhaustNodes = []
                pRecord.exhaustPixie = ''
                for tankType in self.OM.allDesc:
                    if not confDict.get('swap%s' % tankType, getattr(pRecord, 'swap%s' % tankType)):
                        if doPrint:
                            print '%s: %s swapping in %s disabled.' % (self.ID, tankType.lower(), sname)
                        setattr(pRecord, 'swap%s' % tankType,
                                confDict.get('swap%s' % tankType, getattr(pRecord, 'swap%s' % tankType)))
                        for xmlName in self.OM.selected[tankType].keys():
                            if sname == self.OM.selected[tankType][xmlName]:
                                del self.OM.selected[tankType][xmlName]
                        if sname in self.OM.allDesc[tankType]:
                            self.OM.allDesc[tankType].remove(sname)
                        continue
                    if 'use%sWhitelist' % tankType in confDict:
                        pRecord.swapAll[tankType] = not confDict['use%sWhitelist' % tankType]
                    if not pRecord.swapAll[tankType]:
                        templist = confDict['%sWhitelist' % tankType.lower()].split(',')
                        curVehNamesList = []
                        for curVehName in templist:
                            curVehName = curVehName.strip()
                            if curVehName:
                                pRecord.swapAll[tankType] = False
                                curVehNamesList.append(curVehName)
                                if curVehName not in pRecord.whitelists[tankType]:
                                    pRecord.whitelists[tankType].append(curVehName)

                        if self.data['isDebug']:
                            if pRecord.swapAll[tankType]:
                                print '%s: empty whitelist for %s. Apply to all %s tanks' % (
                                    self.ID, sname, tankType.lower())
                            else:
                                print '%s: whitelist for %s: %s' % (self.ID, tankType.lower(), curVehNamesList)
                    if pRecord.swapAll[tankType]:
                        if sname not in self.OM.allDesc[tankType]:
                            self.OM.allDesc[tankType].append(sname)
                            if doPrint:
                                print '%s: %s will be used for all %s tanks if not explicitly designated to another ' \
                                      'model.' % (self.ID, sname, tankType.lower())
                    else:
                        if sname in self.OM.allDesc[tankType]:
                            self.OM.allDesc[tankType].remove(sname)
                        for xmlName in self.OM.selected[tankType].keys():
                            if sname == self.OM.selected[tankType][xmlName] and xmlName not in pRecord.whitelists[tankType]:
                                del self.OM.selected[tankType][xmlName]
                pRecord.strChassis = confDict['chassis']['undamaged']
                pRecord.strHull = confDict['hull']['undamaged']
                pRecord.strTurret = confDict['turret']['undamaged']
                pRecord.strGun = confDict['gun']['undamaged']
                if 'AODecals' in confDict['chassis'] and 'hullPosition' in confDict['chassis']:
                    pRecord.AODecals = readAODecals(confDict['chassis']['AODecals'])
                    pRecord.refHullPosition = Math.Vector3(tuple(confDict['chassis']['hullPosition']))
                    if self.data['isDebug']:
                        print 'RemodEnabler: cfg refHullPosition: '
                        print pRecord.refHullPosition
                else:
                    pRecord.AODecals = None
                if 'exclusionMask' in confDict.get('camouflage', {}):
                    pRecord.strCamoMask = confDict['camouflage']['exclusionMask']
                    if 'tiling' in confDict.get('camouflage', {}):
                        pRecord.strCamoTiling = tuple(confDict['camouflage']['tiling'])
                elif self.data['isDebug']:
                    print '%s: default camomask not found for %s' % (self.ID, sname)
                for key in ('Gun', 'Hull', 'Turret'):
                    setattr(pRecord, 'emblemSlots%s' % key,
                            readEmblemSlots(confDict['%s' % key.lower()].get('emblemSlots', [])))
                    if 'exclusionMask' in confDict[key.lower()].get('camouflage', {}):
                        setattr(pRecord, 'strCamoMask%s' % key,
                                confDict['%s' % key.lower()]['camouflage']['exclusionMask'])
                        if 'tiling' in confDict[key.lower()].get('camouflage', {}):
                            setattr(pRecord, 'strCamoTiling%s' % key,
                                    tuple(confDict['%s' % key.lower()]['camouflage']['tiling']))
                if 'nodes' in confDict['hull'].get('exhaust', {}):
                    pRecord.exhaustNodes = confDict['hull']['exhaust']['nodes'].split()
                if 'pixie' in confDict['hull'].get('exhaust', {}):
                    pRecord.exhaustPixie = confDict['hull']['exhaust']['pixie']
                pRecord.OM_model_chassis = {}
                for key in ('traces', 'tracks', 'wheels', 'groundNodes', 'trackNodes', 'splineDesc', 'trackParams'):
                    pRecord.OM_model_chassis[key] = confDict['chassis'][key]
                if 'effects' in confDict['gun']:
                    pRecord.strGunEffects = confDict['gun']['effects']
                if 'reloadEffect' in confDict['gun']:
                    pRecord.strGunReloadEffect = confDict['gun']['reloadEffect']
                if doPrint:
                    print '%s: config for %s loaded.' % (self.ID, sname)
                pRecord.enabled = True

            if not self.OM.models:
                print '%s: no configs found, model module standing down.' % self.ID
                self.OM.enabled = False
                self.loadJson('remodsCache', self.OM.selected, self.configPath, True)
            else:
                self.OM.remodTanks = {}
                for OMDesc in self.OM.models.values():
                    for tankType, whitelist in OMDesc.whitelists.iteritems():
                        for xmlName in whitelist:
                            self.OM.remodTanks.setdefault(tankType, set()).add(xmlName)
                for tankType in self.OM.allDesc:
                    for xmlName in self.OM.selected[tankType].keys():
                        if (self.OM.selected[tankType][xmlName] and self.OM.selected[tankType][
                            xmlName] not in self.OM.models) or (
                                len(self.OM.allDesc[tankType]) == 1 and xmlName not in self.OM.remodTanks[tankType]):
                            del self.OM.selected[tankType][xmlName]
                if self.OM.selected['Remod'] and self.OM.selected['Remod'] not in self.OM.models:
                    self.OM.selected['Remod'] = ''
                self.loadJson('remodsCache', self.OM.selected, self.configPath, True)
        else:
            print '%s: no remods found, model module standing down.' % self.ID
            self.OM.enabled = False
            self.loadJson('remodsCache', self.OM.selected, self.configPath, True)
        self.OS.enabled = os.path.isdir('%s/vehicles/skins/' % BigWorld.curCV) and len(
            glob.glob('%s/vehicles/skins/*' % BigWorld.curCV))
        if self.OS.enabled:
            self.OS.priorities = self.loadJson('skinsPriority', self.OS.priorities, self.configPath)
            configsPath = '%sskins/*.json' % self.configPath
            for configPath in glob.iglob(configsPath):
                confPath = configPath.replace('%s/' % BigWorld.curCV, '')
                sname = os.path.basename(configPath).split('.')[0]
                self.configsDict[sname] = confDict = self.loadJson(sname, self.configsDict.get(sname, {}),
                                                                   os.path.dirname(configPath) + '/')
                if not confDict:
                    print '%s: error while reading %s.' % (self.ID, os.path.basename(confPath))
                    continue
                if 'enabled' in confDict and not confDict['enabled']:
                    print '%s: %s disabled, moving on' % (self.ID, sname)
                    continue
                texturesPath = '%s/vehicles/skins/textures/%s' % (BigWorld.curCV, sname)
                if not os.path.isdir(texturesPath):
                    print '%s: config and folder mismatch detected: %s folder not found, config deleted' % (
                        self.ID, texturesPath)
                    shutil.rmtree(configPath)
                    continue
                self.OS.models[sname] = pRecord = OSDescriptor()
                pRecord.name = sname
                for tankType in self.OS.priorities:
                    if not confDict.get('swap%s' % tankType, getattr(pRecord, 'swap%s' % tankType)):
                        if doPrint:
                            print '%s: %s swapping in %s disabled.' % (self.ID, tankType, sname)
                        setattr(pRecord, 'swap%s' % tankType,
                                confDict.get('swap%s' % tankType, getattr(pRecord, 'swap%s' % tankType)))
                        if sname in self.OS.priorities[tankType]:
                            self.OS.priorities[tankType].remove(sname)
                        continue
                    if sname not in self.OS.priorities[tankType]:
                        self.OS.priorities[tankType].append(sname)
                pRecord.whitelist = []
                for curNation in glob.iglob(texturesPath + '/vehicles/*'):
                    for vehicleName in glob.iglob(curNation + '/*'):
                        curVehName = os.path.basename(vehicleName)
                        pRecord.hasSegmentTex[curVehName] = False
                        if not len(glob.glob(vehicleName + '/tracks/*.dds')) and os.path.isdir(
                                        vehicleName + '/tracks'):
                            shutil.rmtree(vehicleName + '/tracks')
                        else:
                            pRecord.hasSegmentTex[curVehName] = True
                        if not len(glob.glob(vehicleName + '/*.dds')) and not pRecord.hasSegmentTex[curVehName]:
                            os.rmdir(vehicleName)
                            if self.data['isDebug']:
                                print '%s: %s folder from %s pack is deleted: empty' % (
                                    self.ID, curVehName, sname)
                        else:
                            if curVehName not in pRecord.whitelist:
                                pRecord.whitelist.append(curVehName)

                if doPrint:
                    print '%s: config for %s loaded.' % (self.ID, sname)
                pRecord.enabled = True

            if not self.OS.models:
                print '%s: no skins configs found, skins module standing down.' % self.ID
                self.OS.enabled = False
                for key in self.OS.priorities:
                    self.OS.priorities[key] = []
                self.loadJson('skinsPriority', self.OS.priorities, self.configPath, True)
            else:
                for key in self.OS.priorities:
                    for sname in list(self.OS.priorities[key]):
                        if sname not in self.OS.models:
                            self.OS.priorities[key].remove(sname)
                self.loadJson('skinsPriority', self.OS.priorities, self.configPath, True)
        else:
            print '%s: no skins found, skins module standing down.' % self.ID
            self.OS.enabled = False
            for key in self.OS.priorities:
                self.OS.priorities[key] = []
            self.loadJson('skinsPriority', self.OS.priorities, self.configPath, True)


_gui_config = getattr(mod_PYmodsGUI, 'g_gui', None)
_config = _Config()
_config.load()
texReplaced = {'': False, '_dynamic': False}
skinsFound = {'': False, '_dynamic': False}
clientIsNew = True
skinsModelsMissing = {'': True, '_dynamic': True}
needToReReadSkinsModels = {'': False, '_dynamic': False}
modelsDir = BigWorld.curCV + '/vehicles/skins%s/models/'
skinVehNamesLDict = {'': {}, '_dynamic': {}}


def CRC32_from_file(filename):
    buf = open(filename, 'rb').read()
    buf = binascii.crc32(buf) & 0xFFFFFFFF
    return buf


@async
@process
def skinCRC32All(callback):
    global texReplaced, skinsFound
    for skinsType in texReplaced:
        CRC32cacheFile = '%s/vehicles/skins%s/CRC32_textures.txt' % (BigWorld.curCV, skinsType)
        CRC32cache = None
        if os.path.isfile(CRC32cacheFile):
            CRC32cache = open(CRC32cacheFile, 'rb').read()
        skinsPath = '%s/vehicles/skins/textures/' % BigWorld.curCV
        if os.path.isdir(skinsPath):
            if len(glob.glob(skinsPath + '*')):
                skinsFound[skinsType] = True
                print 'RemodEnabler: listing %s/vehicles/skins%s/textures for CRC32' % (BigWorld.curCV, skinsType)
                CRC32 = 0
                for skin in glob.iglob(skinsPath + '*'):
                    result = yield skinCRC32Process(skin, skinsType)
                    CRC32 ^= result

                if CRC32cache is not None and str(CRC32) == CRC32cache:
                    print 'RemodEnabler: skins textures were not changed'
                else:
                    if CRC32cache is None:
                        print 'RemodEnabler: skins textures were reinstalled (or you deleted the CRC32 cache)'
                    else:
                        print 'RemodEnabler: skins textures were changed'
                    cf = open(CRC32cacheFile, 'w+b')
                    cf.write(str(CRC32))
                    cf.close()
                    texReplaced[skinsType] = True
            else:
                print 'RemodEnabler: skins folder is empty'
        else:
            print 'RemodEnabler: skins folder not found'
    BigWorld.callback(0.0, partial(callback, True))


@async
@process
def skinCRC32Process(skin, skinType, callback):
    CRC32 = 0
    skinName = os.path.basename(skin)
    configPath = '%sskins%s/%s.json' % (_config.configPath, skinType, skinName)
    if not os.path.isfile(configPath):
        print '%s: config %s not found, creating default' % (_config.ID, configPath)
        _config.loadJson(skinName, _config.defaultSkinConfig, '%sskins%s/' % (_config.configPath, skinType), True)
    for nation in glob.iglob(skin + '/vehicles/*'):
        result = yield nationCRC32(nation, skinName, skinType)
        CRC32 ^= result
    BigWorld.callback(0.0, partial(callback, CRC32))


@async
@process
def nationCRC32(nation, skinName, skinType, callback):
    global skinVehNamesLDict
    CRC32 = 0
    for vehicleName in glob.iglob(nation + '/*'):
        vehName = os.path.basename(vehicleName)
        skinVehNamesLDict[skinType].setdefault(vehName, []).append(skinName)
        result = yield vehicleCRC32(vehicleName)
        CRC32 ^= result
    BigWorld.callback(0.0, partial(callback, CRC32))


@async
def vehicleCRC32(vehicleName, callback):
    CRC32 = 0
    for texture in glob.iglob(vehicleName + '/*.dds'):
        result = CRC32_from_file(texture)
        CRC32 ^= result
    BigWorld.callback(0.0, partial(callback, CRC32))


@async
def modelsCheck(callback):
    global clientIsNew, skinsModelsMissing, needToReReadSkinsModels
    for skinsType in texReplaced:
        modelDir = modelsDir % skinsType
        lastVersionPath = '%s/vehicles/skins%s/last_version.txt' % (BigWorld.curCV, skinsType)
        if os.path.isfile(lastVersionPath):
            lastVersion = open(lastVersionPath).read()
            if getClientVersion() == lastVersion:
                clientIsNew = False
            else:
                print 'RemodEnabler: client version changed'
        else:
            print 'RemodEnabler: client version cache not found'

        if os.path.isdir(modelDir):
            if len(glob.glob(modelDir + '*')):
                skinsModelsMissing[skinsType] = False
            else:
                print 'RemodEnabler: skins%s models dir is empty' % skinsType
        else:
            print 'RemodEnabler: skins%s models dir not found' % skinsType
        needToReReadSkinsModels[skinsType] = skinsFound[skinsType] and (
            clientIsNew or skinsModelsMissing[skinsType] or texReplaced[skinsType])
        if clientIsNew:
            if os.path.isdir(modelDir):
                shutil.rmtree(modelDir)
            lastVersionFile = open(lastVersionPath, 'w+')
            lastVersionFile.write(getClientVersion())
            lastVersionFile.close()
        if skinsFound[skinsType] and not os.path.isdir(modelDir):
            os.makedirs(modelDir)
        elif not skinsFound[skinsType] and os.path.isdir(modelDir):
            print 'RemodEnabler: no skins found, deleting %s' % modelDir
            shutil.rmtree(modelDir)
        elif texReplaced[skinsType] and os.path.isdir(modelDir):
            shutil.rmtree(modelDir)
    BigWorld.callback(0.0, partial(callback, True))


@async
@process
def modelsProcess(callback):
    for skinsType in needToReReadSkinsModels:
        skinsVehDict = skinVehNamesLDict[skinsType]
        if needToReReadSkinsModels[skinsType]:
            Waiting.show('Remod: ' + _config.i18n['UI_loading_header_models_unpack'])
            modelFileFormats = ('model', 'primitives', 'visual', 'primitives_processed', 'visual_processed')
            print 'RemodEnabler: starting to unpack vehicles packages for skins%s' % skinsType
            for vehPkgPath in glob.iglob('./res/packages/vehicles*.pkg'):
                allfilesCnt = 0
                filesCnt = 0
                pr = cProfile.Profile()
                pr.enable()
                print 'RemodEnabler: unpacking %s' % os.path.basename(vehPkgPath)
                vehPkg = ZipFile(vehPkgPath)
                for memberFileName in vehPkg.namelist():
                    if 'normal' in memberFileName and os.path.basename(memberFileName).split('.')[1] in modelFileFormats:
                        for skinName in skinsVehDict.get(os.path.normpath(memberFileName).split('\\')[2], []):
                            processMember(memberFileName, skinName, skinsType, vehPkg)
                            filesCnt += 1
                        allfilesCnt += 1
                        if not filesCnt % 300 or not allfilesCnt % 750:
                            yield doFuncCall()
                vehPkg.close()
                pr.disable()
                if _config.data['isDebug']:
                    print allfilesCnt
                    print filesCnt
                    pstats.Stats(pr).print_stats(0)
            Waiting.hide('Remod: ' + _config.i18n['UI_loading_header_models_unpack'])
    BigWorld.callback(0.0, partial(callback, True))


@async
def doFuncCall(callback):
    BigWorld.callback(0.0, partial(callback, None))


# noinspection PyPep8,PyPep8
def processMember(memberFileName, skinName, skinType, vehPkg):
    modelDir = modelsDir % skinType
    skinDir = modelDir.replace('%s/' % BigWorld.curCV, '') + skinName + '/'
    skinsSign = 'vehicles/skins%s/' % skinType
    if 'primitives' in memberFileName:
        vehPkg.extract(memberFileName, modelDir + skinName)
    elif 'model' in memberFileName:
        oldModel = ResMgr.openSection(memberFileName)
        newModelPath = skinDir + memberFileName
        curModel = ResMgr.openSection(newModelPath, True)
        curModel.copy(oldModel)
        if curModel is None:
            print skinDir + memberFileName
        if curModel.has_key('parent') and skinsSign not in curModel['parent'].asString:
            curParent = skinDir + curModel['parent'].asString
            curModel.writeString('parent', curParent.replace('\\', '/'))
        if skinsSign not in curModel['nodefullVisual'].asString:
            curVisual = skinDir + curModel['nodefullVisual'].asString
            curModel.writeString('nodefullVisual', curVisual.replace('\\', '/'))
        curModel.save()
    elif 'visual' in memberFileName:
        oldVisual = ResMgr.openSection(memberFileName)
        newVisualPath = skinDir + memberFileName
        curVisual = ResMgr.openSection(newVisualPath, True)
        curVisual.copy(oldVisual)
        for curName, curSect in curVisual.items():
            if curName != 'renderSet':
                continue
            for curSubName, curSubSect in curSect['geometry'].items():
                if curSubName != 'primitiveGroup':
                    continue
                for curPrimName, curProp in curSubSect['material'].items():
                    if curPrimName != 'property' or not curProp.has_key('Texture'):
                        continue
                    texDir = skinDir.replace('models', 'textures')
                    curTexture = curProp['Texture'].asString
                    if skinsSign not in curTexture and os.path.isfile(texDir + curTexture):
                        curDiff = texDir + curTexture
                        curProp.writeString('Texture', curDiff.replace('\\', '/'))
                    elif skinsSign in curTexture and not os.path.isfile(BigWorld.curCV + curTexture):
                        curDiff = curTexture.replace(texDir, '')
                        curProp.writeString('Texture', curDiff.replace('\\', '/'))

        curVisual.save()


@process
def skinCaller():
    pr = cProfile.Profile()
    pr.enable()
    yield skinCRC32All()
    yield modelsCheck()
    yield modelsProcess()
    pr.disable()
    pstats.Stats(pr).print_stats(0)
    Waiting.hide('Remod: ' + _config.i18n['UI_loading_header_CRC32'])


def new_populate(self):
    old_populate(self)
    if _config.data['enabled']:
        BigWorld.callback(5.0, partial(Waiting.show, 'Remod: ' + _config.i18n['UI_loading_header_CRC32']))
        BigWorld.callback(6.0, skinCaller)


old_populate = LoginView._populate
LoginView._populate = new_populate


def lobbyKeyControl(event):
    try:
        if event.isKeyDown() and not getattr(BigWorld, 'isMSAWPopulated', False):
            if PYmodsCore.checkKeys(_config.data['ChangeViewHotkey']):
                while True:
                    newModeNum = _config.possibleModes.index(
                        _config.data['currentMode']) + 1 if _config.possibleModes.index(
                        _config.data['currentMode']) + 1 < len(_config.possibleModes) else 0
                    _config.data['currentMode'] = _config.possibleModes[newModeNum]
                    if _config.data.get(_config.possibleModes[newModeNum], True):
                        break
                if _config.data['isDebug']:
                    print 'RemodEnabler: Changing display mode to %s' % _config.data['currentMode']
                SystemMessages.pushMessage(
                    'PYmods_SM' + _config.i18n['UI_mode'] + _config.i18n['UI_mode_' + _config.data['currentMode']].join(
                        ('<b>', '</b>.')),
                    SystemMessages.SM_TYPE.Warning)
                g_currentPreviewVehicle.refreshModel()
            if PYmodsCore.checkKeys(_config.data['CollisionHotkey']):
                if _config.data['collisionComparisonEnabled']:
                    _config.data['collisionComparisonEnabled'] = False
                    if _config.data['isDebug']:
                        print 'RemodEnabler: Disabling collision displaying'
                    SystemMessages.pushMessage('PYmods_SM' + _config.i18n['UI_disableCollisionComparison'],
                                               SystemMessages.SM_TYPE.CustomizationForGold)
                elif _config.data['collisionEnabled']:
                    _config.data['collisionEnabled'] = False
                    _config.data['collisionComparisonEnabled'] = True
                    if _config.data['isDebug']:
                        print 'RemodEnabler: Enabling collision display comparison mode'
                    SystemMessages.pushMessage('PYmods_SM' + _config.i18n['UI_enableCollisionComparison'],
                                               SystemMessages.SM_TYPE.CustomizationForGold)
                else:
                    _config.data['collisionEnabled'] = True
                    if _config.data['isDebug']:
                        print 'RemodEnabler: Enabling collision display'
                    SystemMessages.pushMessage('PYmods_SM' + _config.i18n['UI_enableCollision'],
                                               SystemMessages.SM_TYPE.CustomizationForGold)
                g_currentPreviewVehicle.refreshModel()
            if PYmodsCore.checkKeys(_config.data['SwitchRemodHotkey']):
                if _config.data['currentMode'] != 'remod':
                    curTankType = _config.data['currentMode'][0].upper() + _config.data['currentMode'][1:]
                    snameList = sorted(_config.OM.models.keys()) + ['']
                    if _config.OM.selected[curTankType].get(_config.curVehicleName) not in snameList:
                        snameIdx = 0
                    else:
                        snameIdx = snameList.index(_config.OM.selected[curTankType][_config.curVehicleName]) + 1
                        if snameIdx == len(snameList):
                            snameIdx = 0
                    for Idx in xrange(snameIdx, len(snameList)):
                        curPRecord = _config.OM.models.get(snameList[Idx])
                        if snameList[Idx] not in _config.OM.allDesc[curTankType] and _config.curVehicleName not in \
                                curPRecord.whitelists[curTankType]:
                            continue
                        else:
                            if _config.curVehicleName in _config.OM.remodTanks[curTankType] or len(
                                    _config.OM.allDesc[curTankType]) > 1:
                                _config.OM.selected[curTankType][_config.curVehicleName] = getattr(curPRecord, 'name', '')
                            _config.loadJson('remodsCache', _config.OM.selected, _config.configPath, True)
                            break
                    g_currentPreviewVehicle.refreshModel()
                else:
                    snameList = sorted(_config.OM.models.keys())
                    if _config.OM.selected['Remod'] not in snameList:
                        snameIdx = 0
                    else:
                        snameIdx = snameList.index(_config.OM.selected['Remod']) + 1
                        if snameIdx == len(snameList):
                            snameIdx = 0
                    curPRecord = _config.OM.models[snameList[snameIdx]]
                    _config.OM.selected['Remod'] = curPRecord.name
                    _config.loadJson('remodsCache', _config.OM.selected, _config.configPath, True)
                    g_currentPreviewVehicle.refreshModel()
    except StandardError:
        traceback.print_exc()


def inj_hkKeyEvent(event):
    LobbyApp = g_appLoader.getDefLobbyApp()
    try:
        if LobbyApp and _config.data['enabled']:
            lobbyKeyControl(event)
    except StandardError:
        print 'RemodEnabler: ERROR at inj_hkKeyEvent\n%s' % traceback.print_exc()


InputHandler.g_instance.onKeyDown += inj_hkKeyEvent
InputHandler.g_instance.onKeyUp += inj_hkKeyEvent


def new_obj(cls, *args):
    try:
        return old_obj(cls, *args)
    except StandardError:
        if _config.data['isDebug']:
            print 'Cannot directly construct objects of this type: %s' % cls


old_obj = copy_reg.__newobj__
copy_reg.__newobj__ = new_obj


def OM_find(xmlName, playerName, isPlayerVehicle, isAlly, currentMode='battle'):
    _config.OMDesc = None
    if _config.data['isDebug']:
        if not isPlayerVehicle:
            print 'RemodEnabler: looking for OMDescriptor for %s, player - %s' % (xmlName, playerName)
        else:
            print 'RemodEnabler: looking for OMDescriptor for %s' % xmlName
    curTankType = 'Player' if isPlayerVehicle else 'Ally' if isAlly else 'Enemy'
    if currentMode != 'remod':
        snameList = sorted(_config.OM.models.keys()) + ['']
        if _config.OM.selected[curTankType].get(xmlName) not in snameList:
            snameIdx = 0
        else:
            snameIdx = snameList.index(_config.OM.selected[curTankType][xmlName])
        for Idx in xrange(snameIdx, len(snameList)):
            curPRecord = _config.OM.models.get(snameList[Idx])
            if snameList[Idx] not in _config.OM.allDesc[curTankType] and xmlName not in curPRecord.whitelists[curTankType]:
                continue
            else:
                if xmlName in _config.OM.remodTanks[curTankType] or len(_config.OM.allDesc[curTankType]) > 1:
                    _config.OM.selected[curTankType][xmlName] = getattr(curPRecord, 'name', '')
                _config.OMDesc = curPRecord
                break

        # noinspection PyUnboundLocalVariable
        if _config.OMDesc is None and snameList[Idx] and xmlName in _config.OM.selected[curTankType]:
            del _config.OM.selected[curTankType][xmlName]
        _config.loadJson('remodsCache', _config.OM.selected, _config.configPath, True)
    else:
        snameList = sorted(_config.OM.models.keys())
        if _config.OM.selected['Remod'] not in snameList:
            snameIdx = 0
        else:
            snameIdx = snameList.index(_config.OM.selected['Remod'])
        curPRecord = _config.OM.models[snameList[snameIdx]]
        _config.OMDesc = curPRecord
        _config.OM.selected['Remod'] = curPRecord.name
        _config.loadJson('remodsCache', _config.OM.selected, _config.configPath, True)


def OM_apply(vDesc):
    xmlName = vDesc.name.split(':')[1].lower()
    if _config.data['isDebug']:
        print 'RemodEnabler: %s assigned to %s' % (xmlName, _config.OMDesc.name)
        print '!! swapping chassis '
    for key in ('splineDesc', 'trackParams'):
        if vDesc.chassis[key] is None:
            vDesc.chassis[key] = {}
    for key in ('traces', 'tracks', 'wheels', 'groundNodes', 'trackNodes', 'splineDesc', 'trackParams'):
        exec "vDesc.chassis['%s']=" % key + _config.OMDesc.OM_model_chassis[key]
    if _config.OMDesc.AODecals:
        AODecalsOffset = vDesc.chassis['hullPosition'] - _config.OMDesc.refHullPosition
        vDesc.chassis['AODecals'] = copy.deepcopy(_config.OMDesc.AODecals)
        vDesc.chassis['AODecals'][0].setElement(3, 1, AODecalsOffset.y)
    elif _config.data['isDebug']:
        print 'warning: AODecals not found. stock AODecal is applied'
        print vDesc.chassis['AODecals'][0].translation
    vDesc.chassis['models']['undamaged'] = _config.OMDesc.strChassis
    vDesc.hull['models']['undamaged'] = _config.OMDesc.strHull
    vDesc.turret['models']['undamaged'] = _config.OMDesc.strTurret
    vDesc.gun['models']['undamaged'] = _config.OMDesc.strGun
    if _config.OMDesc.strGunEffects != '':
        newGunEffects = vehicles.g_cache._gunEffects.get(_config.OMDesc.strGunEffects)
        if newGunEffects:
            vDesc.gun['effects'] = newGunEffects
    if _config.OMDesc.strGunReloadEffect != '':
        newGunReloadEffect = vehicles.g_cache._gunReloadEffects.get(_config.OMDesc.strGunReloadEffect, None)
        if newGunReloadEffect:
            vDesc.gun['reloadEffect'] = newGunReloadEffect
    if _config.OMDesc.emblemSlotsGun is not None:
        vDesc.gun['emblemSlots'] = _config.OMDesc.emblemSlotsGun
    else:
        vDesc.gun['emblemSlots'] = []
    cntClan = 1
    cntPlayer = cntInscription = 0
    if _config.OMDesc.emblemSlotsHull is None:
        if _config.data['isDebug']:
            print 'RemodEnabler: hull and turret emblemSlots not provided.'
    else:
        if _config.data['isDebug']:
            print 'RemodEnabler: hull and turret emblemSlots found. processing'
        for slot in vDesc.hull['emblemSlots']:
            if slot.type == 'inscription':
                cntInscription += 1
            if slot.type == 'player':
                cntPlayer += 1

        for slot in vDesc.turret['emblemSlots']:
            if slot.type == 'inscription':
                cntInscription += 1
            if slot.type == 'player':
                cntPlayer += 1

        try:
            vDesc.hull['emblemSlots'] = []
            vDesc.turret['emblemSlots'] = []
            for slot in _config.OMDesc.emblemSlotsHull:
                if slot.type == 'player' and cntPlayer > 0:
                    vDesc.hull['emblemSlots'].append(slot)
                    cntPlayer -= 1
                if slot.type == 'inscription' and cntInscription > 0:
                    vDesc.hull['emblemSlots'].append(slot)
                    cntInscription -= 1
                if slot.type == 'clan' and cntClan > 0:
                    vDesc.hull['emblemSlots'].append(slot)
                    cntClan -= 1

            for slot in _config.OMDesc.emblemSlotsTurret:
                if slot.type == 'player' and cntPlayer > 0:
                    vDesc.turret['emblemSlots'].append(slot)
                    cntPlayer -= 1
                if slot.type == 'inscription' and cntInscription > 0:
                    vDesc.turret['emblemSlots'].append(slot)
                    cntInscription -= 1
                if slot.type == 'clan' and cntClan > 0:
                    vDesc.turret['emblemSlots'].append(slot)
                    cntClan -= 1

            assert not cntClan and not cntPlayer and not cntInscription
            if _config.data['isDebug']:
                print 'RemodEnabler: hull and turret emblemSlots swap completed'
        except StandardError:
            print 'RemodEnabler: provided emblem slots corrupted. Stock slots restored'
            if _config.data['isDebug']:
                print 'cntPlayer=' + str(cntPlayer)
                print 'cntInscription=' + str(cntInscription)
    if _config.OMDesc.emblemSlotsHull is None:
        for i in range(len(vDesc.hull['emblemSlots'])):
            vDesc.hull['emblemSlots'][i] = vDesc.hull['emblemSlots'][i]._replace(size=0.001)

    if _config.OMDesc.emblemSlotsTurret is None:
        for i in range(len(vDesc.turret['emblemSlots'])):
            vDesc.turret['emblemSlots'][i] = vDesc.turret['emblemSlots'][i]._replace(size=0.001)

    vDesc.type.camouflageExclusionMask = _config.OMDesc.strCamoMask
    if _config.OMDesc.strCamoMaskHull != '':
        vDesc.hull['camouflageExclusionMask'] = _config.OMDesc.strCamoMaskHull
    if _config.OMDesc.strCamoMaskTurret != '':
        vDesc.turret['camouflageExclusionMask'] = _config.OMDesc.strCamoMaskTurret
    if _config.OMDesc.strCamoMaskGun != '':
        vDesc.gun['camouflageExclusionMask'] = _config.OMDesc.strCamoMaskGun
    if hasattr(_config.OMDesc, 'strCamoTiling'):
        vDesc.type.camouflageTiling = _config.OMDesc.strCamoTiling
    if hasattr(_config.OMDesc, 'strCamoTilingHull'):
        vDesc.hull['camouflageTiling'] = _config.OMDesc.strCamoTilingHull
    if hasattr(_config.OMDesc, 'strCamoTilingTurret'):
        vDesc.turret['camouflageTiling'] = _config.OMDesc.strCamoTilingTurret
    if hasattr(_config.OMDesc, 'strCamoTilingGun'):
        vDesc.gun['camouflageTiling'] = _config.OMDesc.strCamoTilingGun
    if _config.OMDesc.exhaustNodes:
        vDesc.hull['exhaust'].nodes = _config.OMDesc.exhaustNodes
    return vDesc


def OS_find(curVehName, playerName, isPlayerVehicle, isAlly, currentMode='battle'):
    _config.OSDesc = None
    if _config.data['isDebug']:
        if not isPlayerVehicle:
            print 'RemodEnabler: looking for OSDescriptor for %s, player - %s' % (curVehName, playerName)
        else:
            print 'RemodEnabler: looking for OSDescriptor for %s' % curVehName
    curTankType = 'Player' if isPlayerVehicle else 'Ally' if isAlly else 'Enemy'
    if currentMode != 'remod':
        for curSName in _config.OS.priorities[curTankType]:
            curPRecord = _config.OS.models[curSName]
            if curVehName not in curPRecord.whitelist:
                continue
            else:
                _config.OSDesc = curPRecord
                break


def OS_apply(vDesc):
    xmlName = vDesc.name.split(':')[1].lower()
    sname = _config.OSDesc.name
    if _config.data['isDebug']:
        print 'RemodEnabler: %s assigned to skin %s' % (xmlName, sname)
    for part in TankPartNames.ALL:
        if os.path.isfile(BigWorld.curCV + '/' + getattr(vDesc, part)['models']['undamaged'].replace(
                'vehicles/', 'vehicles/skins/models/%s/vehicles/' % sname)):
            getattr(vDesc, part)['models']['undamaged'] = getattr(vDesc, part)['models']['undamaged'].replace(
                'vehicles/', 'vehicles/skins/models/%s/vehicles/' % sname)
    return vDesc


def printOldConfigs(vDesc):
    xmlName = vDesc.name.split(':')[1].lower()
    print 'old chassis configuration:'
    print vDesc.chassis['traces']
    print vDesc.chassis['tracks']
    print vDesc.chassis['wheels']
    print vDesc.chassis['groundNodes']
    print vDesc.chassis['trackNodes']
    print vDesc.chassis['splineDesc']
    print vDesc.chassis['trackParams']
    print 'old gun emblem slots configuration:'
    print vDesc.gun['emblemSlots']
    print 'old hull emblem slots configuration:'
    print vDesc.hull['emblemSlots']
    print 'old turret emblem slots configuration:'
    print vDesc.turret['emblemSlots']
    _config.data['oldConfigPrints'].append(xmlName)


def new_prerequisites(self, respawnCompactDescr=None):
    if self.respawnCompactDescr is not None:
        respawnCompactDescr = self.respawnCompactDescr
        self.isCrewActive = True
        self.respawnCompactDescr = None
    if respawnCompactDescr is None and self.typeDescriptor is not None:
        return ()
    descr = self.getDescr(respawnCompactDescr)
    if _config.data['enabled']:
        isPlayerVehicle = self.id == BigWorld.player().playerVehicleID
        xmlName = descr.name.split(':')[1].lower()
        playerName = BigWorld.player().arena.vehicles.get(self.id)['name']
        isAlly = BigWorld.player().arena.vehicles.get(self.id)['team'] == BigWorld.player().team
        OM_find(xmlName, playerName, isPlayerVehicle, isAlly)
        vDesc = descr
        if xmlName not in _config.data['oldConfigPrints'] and (
                isPlayerVehicle and _config.OMDesc is None or _config.OMDesc is not None) and _config.data['isDebug']:
            printOldConfigs(vDesc)
        if _config.OMDesc is None:
            if skinsFound['']:
                OS_find(vDesc.chassis['models']['undamaged'].split('/')[2], playerName, isPlayerVehicle, isAlly)
                if _config.OSDesc is not None:
                    if _config.data['isDebug']:
                        print 'RemodEnabler: !!making a deepcopy for typeDescriptor(inside ' \
                              'CompoundAppearance::prerequisites)'
                    vDesc = copy.deepcopy(vDesc)
                    vDesc = OS_apply(vDesc)
                elif _config.data['isDebug']:
                    print 'RemodEnabler: %s unchanged' % xmlName
            elif _config.data['isDebug']:
                print 'RemodEnabler: %s unchanged' % xmlName
        else:
            if _config.data['isDebug']:
                print 'RemodEnabler: !!making a deepcopy for typeDescriptor(inside CompoundAppearance::prerequisites)'
            vDesc = copy.deepcopy(vDesc)
            vDesc = OM_apply(vDesc)
        self.typeDescriptor = vDesc
    else:
        self.typeDescriptor = descr
    self.appearance, compoundAssembler, prereqs = appearance_cache.createAppearance(self.id, self.typeDescriptor,
                                                                                    self.health, self.isCrewActive,
                                                                                    self.isTurretDetached)
    return prereqs


def new_startBuild(self, vDesc, vState):
    if _config.data['enabled']:
        xmlName = vDesc.name.split(':')[1].lower()
        _config.curVehicleName = xmlName
        OM_find(xmlName, 'HangarEntity', _config.data['currentMode'] in 'player', _config.data['currentMode'] in 'ally',
                _config.data['currentMode'])
        if xmlName not in _config.data['oldConfigPrints'] and _config.data['isDebug']:
            printOldConfigs(vDesc)
        if _config.OMDesc is None:
            if skinsFound['']:
                OS_find(vDesc.chassis['models']['undamaged'].split('/')[2], 'HangarEntity',
                        _config.data['currentMode'] in 'player', _config.data['currentMode'] in 'ally',
                        _config.data['currentMode'])
                if _config.OSDesc is not None:
                    if _config.data['isDebug']:
                        print 'RemodEnabler: !!making a deepcopy for typeDescriptor(inside ' \
                              '_VehicleAppearance::prerequisites)'
                    vDesc = copy.deepcopy(vDesc)
                    vDesc = OS_apply(vDesc)
                    if not _config.data['collisionEnabled'] and not _config.data['collisionComparisonEnabled']:
                        SystemMessages.pushMessage(
                            'PYmods_SM' + _config.i18n['UI_install_skin'] + _config.OSDesc.name.join(('<b>', '</b>.')),
                            SystemMessages.SM_TYPE.CustomizationForGold)
                else:
                    if _config.data['isDebug']:
                        print 'RemodEnabler: %s unchanged' % xmlName
                    if not _config.data['collisionEnabled'] and not _config.data['collisionComparisonEnabled']:
                        SystemMessages.pushMessage('PYmods_SM' + _config.i18n['UI_install_default'],
                                                   SystemMessages.SM_TYPE.CustomizationForGold)
            else:
                if _config.data['isDebug']:
                    print 'RemodEnabler: %s unchanged' % xmlName
                if not _config.data['collisionEnabled'] and not _config.data['collisionComparisonEnabled']:
                    SystemMessages.pushMessage('PYmods_SM' + _config.i18n['UI_install_default'],
                                               SystemMessages.SM_TYPE.CustomizationForGold)
        else:
            if _config.data['isDebug']:
                print 'RemodEnabler: !!making a deepcopy for typeDescriptor(inside _VehicleAppearance::prerequisites)'
            vDesc = copy.deepcopy(vDesc)
            vDesc = OM_apply(vDesc)
            if not _config.data['collisionEnabled'] and not _config.data['collisionComparisonEnabled']:
                SystemMessages.pushMessage(
                    'PYmods_SM' + _config.i18n['UI_install_remod'] + _config.OMDesc.name.join(('<b>', '</b>.')),
                    SystemMessages.SM_TYPE.CustomizationForGold)
    old_startBuild(self, vDesc, vState)


def new_setupModel(self, buildIdx):
    old_setupModel(self, buildIdx)
    if _config.data['enabled']:
        vEntityId = self._VehicleAppearance__vEntityId
        vEntity = BigWorld.entity(vEntityId)
        vDesc = self._VehicleAppearance__vDesc
        model = vEntity.model
        self.collisionLoaded = True
        self.modifiedModelsDesc = dict(
            [(part, {'model': None, 'motor': None, 'matrix': None}) for part in TankPartNames.ALL])
        failList = []
        for modelName in self.modifiedModelsDesc.keys():
            try:
                self.modifiedModelsDesc[modelName]['model'] = BigWorld.Model(
                    getattr(vDesc, modelName)['hitTester'].bspModelName)
                self.modifiedModelsDesc[modelName]['model'].visible = False
            except StandardError:
                self.collisionLoaded = False
                failList.append(getattr(vDesc, modelName)['hitTester'].bspModelName)

        if failList:
            print 'RemodEnabler: collision load failed: models not found'
            print failList
        if self.collisionLoaded:
            if any((_config.data['collisionEnabled'], _config.data['collisionComparisonEnabled'])):
                # Getting offset matrices
                hullOffset = mathUtils.createTranslationMatrix(vEntity.typeDescriptor.chassis['hullPosition'])
                turretOffset = mathUtils.createTranslationMatrix(vEntity.typeDescriptor.hull['turretPositions'][0])
                gunOffset = mathUtils.createTranslationMatrix(vEntity.typeDescriptor.turret['gunPosition'])
                # Getting local transform matrices
                hullMP = mathUtils.MatrixProviders.product(mathUtils.createIdentityMatrix(), hullOffset)
                turretMP = mathUtils.MatrixProviders.product(mathUtils.createIdentityMatrix(), turretOffset)
                gunMP = mathUtils.MatrixProviders.product(mathUtils.createIdentityMatrix(), gunOffset)
                # turretMP = mathUtils.MatrixProviders.product(vEntity.appearance.turretMatrix, turretOffset)
                # gunMP = mathUtils.MatrixProviders.product(vEntity.appearance.gunMatrix, gunOffset)
                # Getting full transform matrices relative to vehicle coordinate system
                self.modifiedModelsDesc[TankPartNames.CHASSIS][
                    'matrix'] = fullChassisMP = mathUtils.createIdentityMatrix()
                self.modifiedModelsDesc[TankPartNames.HULL]['matrix'] = fullHullMP = mathUtils.MatrixProviders.product(
                    hullMP, fullChassisMP)
                self.modifiedModelsDesc[TankPartNames.TURRET][
                    'matrix'] = fullTurretMP = mathUtils.MatrixProviders.product(turretMP, fullHullMP)
                self.modifiedModelsDesc[TankPartNames.GUN]['matrix'] = mathUtils.MatrixProviders.product(gunMP,
                                                                                                         fullTurretMP)
                for moduleName, module in self.modifiedModelsDesc.items():
                    if module['motor'] not in tuple(module['model'].motors):
                        module['motor'] = BigWorld.Servo(
                            mathUtils.MatrixProviders.product(module['matrix'], vEntity.matrix))
                        module['model'].addMotor(module['motor'])
                    if module['model'] not in tuple(vEntity.models):
                        try:
                            vEntity.addModel(module['model'])
                        except StandardError:
                            pass
                    module['model'].visible = True
                addCollisionGUI(self)
            if _config.data['collisionEnabled']:
                for moduleName in TankPartNames.ALL:
                    if model.node(moduleName) is not None:
                        scaleMat = Math.Matrix()
                        scaleMat.setScale((0.001, 0.001, 0.001))
                        model.node(moduleName, scaleMat)
                    else:
                        print 'RemodEnabler_hangarChameleon: %s not found' % moduleName


old_prerequisites = Vehicle.prerequisites
Vehicle.prerequisites = new_prerequisites
old_startBuild = _VehicleAppearance._VehicleAppearance__startBuild
_VehicleAppearance._VehicleAppearance__startBuild = new_startBuild
old_setupModel = _VehicleAppearance._VehicleAppearance__setupModel
_VehicleAppearance._VehicleAppearance__setupModel = new_setupModel


def clearCollision(self):
    if _config.data['enabled']:
        vEntityId = self._VehicleAppearance__vEntityId
        if getattr(self, 'collisionLoaded', False):
            for moduleName, module in self.modifiedModelsDesc.items():
                if module['model'] in tuple(BigWorld.entity(vEntityId).models):
                    BigWorld.entity(vEntityId).delModel(module['model'])
                    if module['motor'] in tuple(module['model'].motors):
                        module['model'].delMotor(module['motor'])
        delCollisionGUI(self)


def new_refresh(self):
    clearCollision(self)
    old_refresh(self)


def new_recreate(self, vDesc, vState, onVehicleLoadedCallback=None):
    clearCollision(self)
    old_recreate(self, vDesc, vState, onVehicleLoadedCallback)


old_refresh = _VehicleAppearance.refresh
_VehicleAppearance.refresh = new_refresh
old_recreate = _VehicleAppearance.recreate
_VehicleAppearance.recreate = new_recreate


class TextBox(object):
    def __init__(self, text='', position=(0, 0.6, 1), colour=(255, 255, 255, 255), size=(0, 0.04)):
        self.GUIComponent = GUI.Text('')
        self.GUIComponent.verticalAnchor = 'CENTER'
        self.GUIComponent.horizontalAnchor = 'CENTER'
        self.GUIComponent.verticalPositionMode = 'CLIP'
        self.GUIComponent.horizontalPositionMode = 'CLIP'
        self.GUIComponent.materialFX = 'BLEND'
        self.GUIComponent.colour = colour
        self.GUIComponent.heightMode = 'CLIP'
        self.GUIComponent.explicitSize = True
        self.GUIComponent.position = position
        self.GUIComponent.font = 'collision_table'
        self.GUIComponent.text = text
        self.GUIComponent.size = size
        self.GUIComponent.visible = True

    def addRoot(self):
        GUI.addRoot(self.GUIComponent)

    def delRoot(self):
        GUI.delRoot(self.GUIComponent)

    def __del__(self):
        self.delRoot()


class TexBox(object):
    def __init__(self, texturePath='', position=(0, 0, 1), size=(0.09, 0.045)):
        self.GUIComponent = GUI.Simple('')
        self.GUIComponent.verticalAnchor = 'CENTER'
        self.GUIComponent.horizontalAnchor = 'CENTER'
        self.GUIComponent.verticalPositionMode = 'CLIP'
        self.GUIComponent.horizontalPositionMode = 'CLIP'
        self.GUIComponent.widthMode = 'CLIP'
        self.GUIComponent.heightMode = 'CLIP'
        self.GUIComponent.materialFX = 'BLEND'
        self.GUIComponent.colour = (255, 255, 255, 255)
        self.GUIComponent.size = size
        self.GUIComponent.position = position
        self.GUIComponent.textureName = texturePath
        self.GUIComponent.mapping = ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0))
        self.GUIComponent.visible = True

    def addRoot(self):
        GUI.addRoot(self.GUIComponent)

    def delRoot(self):
        GUI.delRoot(self.GUIComponent)

    def __del__(self):
        self.delRoot()


def addCollisionGUI(self):
    vDesc = self._VehicleAppearance__vDesc
    self.collisionTable = {}
    for moduleIdx, moduleName in enumerate(TankPartNames.ALL):
        self.collisionTable[moduleName] = curCollisionTable = {'textBoxes': [],
                                                               'texBoxes': [],
                                                               'armorValues': {}}
        module = getattr(vDesc, moduleName)
        for Idx, groupNum in enumerate(sorted(module['materials'].keys())):
            armorValue = int(module['materials'][groupNum].armor)
            curCollisionTable['armorValues'].setdefault(armorValue, [])
            if groupNum not in curCollisionTable['armorValues'][armorValue]:
                curCollisionTable['armorValues'][armorValue].append(groupNum)
        for Idx, armorValue in enumerate(sorted(curCollisionTable['armorValues'], reverse=True)):
            x = (6 + moduleIdx) / 10.0 + 0.025
            y = (-1 - Idx) / 20.0 - 0.002
            textBox = TextBox('%s' % armorValue, (x, y, 0.7), (240, 240, 240, 255))
            textBox.addRoot()
            curCollisionTable['textBoxes'].append(textBox)
            textBox = TextBox('%s' % armorValue, (x, y, 0.725), (0, 0, 0, 255))
            textBox.addRoot()
            curCollisionTable['textBoxes'].append(textBox)
            # texBox = TexBox('system/fonts/collision_table_shadow.dds', (x, y, 0.75),
            #                 (textBox.GUIComponent.width + 0.008, textBox.GUIComponent.height + 0.01))
            # texBox.addRoot()
            # curCollisionTable['texBoxes'].append(texBox)
            '''
            for i in xrange(3):
                armorStr = '%s' % armorValue
                for idx, num in enumerate(armorStr):
                    colWidth = 0.01
                    colPad = colWidth / 2.0
                    x = (6 + moduleIdx) / 10.0 + 0.025 - colPad * float(len(armorStr) - 1) + colWidth * idx
                    y = (-3 - Idx) / 20.0
                    textBox = TextBox('%s' % num, (x, y, (7 - i % 2) / 10.0),
                                      (0.016 - 0.006 * i, 0.05 - 0.015 * i), tuple(255 * (i % 2) if j <= 2 else 255
                                      for j in xrange(4)))
                    textBox.addRoot()
                    curCollisionTable['textBoxes'].append(textBox)
            '''
            kindsCap = len(curCollisionTable['armorValues'][armorValue])
            for matIdx, matKind in enumerate(curCollisionTable['armorValues'][armorValue]):
                matKindName = material_kinds.IDS_BY_NAMES.keys()[material_kinds.IDS_BY_NAMES.values().index(matKind)]
                texName = 'objects/misc/collisions_mat/%s.dds' % matKindName
                colWidth = 0.09
                colPad = colWidth / 2.0
                x = (6 + moduleIdx) / 10.0 + 0.025 - colPad + colPad / float(kindsCap) + (
                    colWidth / float(kindsCap) * float(matIdx))
                y = (-1 - Idx) / 20.0
                texBox = TexBox(texName, (x, y, 0.8), (0.09 / float(kindsCap), 0.045))
                texBox.addRoot()
                curCollisionTable['texBoxes'].append(texBox)
        GUI.reSort()


def delCollisionGUI(self):
    if hasattr(self, 'collisionTable'):
        del self.collisionTable


class Analytics(PYmodsCore.Analytics):
    def __init__(self):
        super(Analytics, self).__init__()
        self.mod_description = 'RemodEnabler'
        self.mod_id_analytics = 'UA-76792179-4'
        self.mod_version = '2.0.0'


statistic_mod = Analytics()


def fini():
    try:
        statistic_mod.end()
    except StandardError:
        traceback.print_exc()


def new_LW_populate(self):
    old_LW_populate(self)
    try:
        statistic_mod.start()
    except StandardError:
        traceback.print_exc()


old_LW_populate = LobbyView._populate
LobbyView._populate = new_LW_populate
