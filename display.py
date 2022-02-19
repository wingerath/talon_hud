from talon import Context, Module, actions, app, skia, cron, ctrl, scope, canvas, registry, settings, ui
from talon.types.point import Point2d
import os
import time
import numpy

from typing import Any
from user.talon_hud.preferences import HeadUpDisplayUserPreferences
from user.talon_hud.theme import HeadUpDisplayTheme
from user.talon_hud.event_dispatch import HeadUpEventDispatch
from user.talon_hud.widget_manager import HeadUpWidgetManager
from user.talon_hud.content.state import hud_content
from user.talon_hud.content.status_bar_poller import StatusBarPoller
from user.talon_hud.content.history_poller import HistoryPoller
from user.talon_hud.layout_widget import LayoutWidget
from user.talon_hud.widgets.textpanel import HeadUpTextPanel
from user.talon_hud.widgets.choicepanel import HeadUpChoicePanel
from user.talon_hud.widgets.contextmenu import HeadUpContextMenu
from user.talon_hud.content.typing import HudPanelContent, HudButton
from user.talon_hud.content.poller import Poller
from user.talon_hud.utils import string_to_speakable_string


# Taken from knausj/code/numbers to make Talon HUD standalone
# The numbers should realistically stay very low for choices, because you don't want choice overload for the user, up to 100
digits = "zero one two three four five six seven eight nine".split()
teens = "ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
tens = "twenty thirty forty fifty sixty seventy eighty ninety".split()
digits_without_zero = digits[1:]
numerical_choice_strings = []
numerical_choice_strings.extend(digits_without_zero)
numerical_choice_strings.extend(teens)
for index, ten in enumerate(tens):
    numerical_choice_strings.append(ten)
    for digit in digits_without_zero:
       numerical_choice_strings.append(ten + " " + digit)
numerical_choice_strings.append("hundred")

ctx = Context()
mod = Module()
mod.list("talon_hud_widget_names", desc="List of available widgets by name linked to their identifier")
mod.list("talon_hud_widget_options", desc="List of options available to the widgets")
mod.list("talon_hud_choices", desc="Available choices shown on screen")
mod.list("talon_hud_themes", desc="Available themes for the Talon HUD")
mod.list("talon_hud_numerical_choices", desc="Available choices shown on screen numbered")
mod.list("talon_hud_quick_choices", desc="List of widgets with their quick options")
mod.tag("talon_hud_available", desc="Tag that shows the availability of the Talon HUD repository for other scripts")
mod.tag("talon_hud_visible", desc="Tag that shows that the Talon HUD is visible")
mod.tag("talon_hud_choices_visible", desc="Tag that shows there are choices available on screen that can be chosen")
mod.setting("talon_hud_environment", type="string", desc="Which environment to set the HUD in - Useful for setting up a HUD for screen recording or other tasks")

ctx.tags = ['user.talon_hud_available']
ctx.settings['user.talon_hud_environment'] = ""

# A list of Talon HUD versions that can be used to check for in other packages
TALON_HUD_RELEASE_030 = 3 # Walk through version
TALON_HUD_RELEASE_040 = 4 # Multi-monitor version
TALON_HUD_RELEASE_050 = 5 # Debugging / screen overlay release
@mod.scope
def scope():
    return {"talon_hud_version": TALON_HUD_RELEASE_050}

class HeadUpDisplay:
    enabled = False
    display_state = None
    preferences = None
    theme = None
    event_dispatch = None
    pollers = []
    keep_alive_pollers = [] # These pollers will only deactivate when the hud deactivates    
    disable_poller_job = None
    show_animations = False
    widgets = []
    choices_visible = False
    
    prev_mouse_pos = None
    mouse_poller = None
    current_talon_hud_environment = ""
    update_context_debouncer = None
    update_environment_debouncer = None
    
    def __init__(self, display_state, preferences):
        self.display_state = display_state
        self.preferences = preferences
        self.pollers = {}
        self.disable_poller_job = None
        self.theme = HeadUpDisplayTheme(self.preferences.prefs['theme_name'])
        self.event_dispatch = HeadUpEventDispatch()
        self.show_animations = self.preferences.prefs['show_animations']
        self.widget_manager = HeadUpWidgetManager(self.preferences, self.theme, self.event_dispatch)
        
        # These pollers should always be active and available when reloading Talon HUD
        self.pollers = {
            'status': StatusBarPoller(),
            'history': HistoryPoller()
        }
        self.keep_alive_pollers = ['status', 'history']
        
        # Uncomment the line below to add language icons by default
        # self.subscribe_content_id('status_bar', 'language')       
        
    def start(self):
        # Uncomment the line below to add the single click mic toggle by default
        # actions.user.hud_add_single_click_mic_toggle()
        
        if (self.preferences.prefs['enabled']):
            self.enable()
            
            if actions.sound.active_microphone() == "None":
                actions.user.hud_add_log("warning", "Microphone is set to 'None'!\n\nNo voice commands will be registered.")
    
    def enable(self, persisted=False):
        if not self.enabled:
            self.enabled = True
            
            # Only reset the talon hud environment after a user action
            if persisted:
                self.current_talon_hud_environment = settings.get("user.talon_hud_environment")
            
            # Connect the events relating to non-content communication
            self.event_dispatch.register('persist_preferences', self.persist_widgets_preferences)
            self.event_dispatch.register('hide_context_menu', self.hide_context_menu)
            self.event_dispatch.register('deactivate_poller', self.deactivate_poller)
            self.event_dispatch.register('show_context_menu', self.move_context_menu)      
            
            attached_topics = list(self.keep_alive_pollers)
            for widget in self.widget_manager.widgets:
                if widget.preferences.enabled and not widget.enabled:
                    widget.enable()
                    if widget.topic:
                    	attached_topics.append(widget.topic)

            for topic, poller in self.pollers.items():
            	if topic in attached_topics:
                    poller.enable()

            # Reload the preferences just in case a screen change happened in between the hidden state
            if persisted:
                self.reload_preferences()
            
            self.display_state.register('content_update', self.content_update)
            self.display_state.register('panel_update', self.panel_update)            
            self.display_state.register('log_update', self.log_update)
            self.display_state.register('log_revise', self.log_revise)
            ui.register('screen_change', self.reload_preferences)
            settings.register("user.talon_hud_environment", self.hud_environment_change)
            self.determine_active_setup_mouse()
            if persisted:
                self.preferences.persist_preferences({'enabled': True})
                
            # TODO SHOULD PROBABLY FIX THIS FLOW TO MAKE SURE THE CONTENT IS PROPERLY REUSED IN THE WIDGETS INSTEAD
            actions.user.hud_refresh_content()
            
            # Make sure context isn't updated in this thread because of automatic reloads
            cron.cancel(self.update_context_debouncer)
            self.update_context_debouncer = cron.after("50ms", self.update_context)

    def disable(self, persisted=False):
        if self.enabled:
            self.enabled = False
            
            for widget in self.widget_manager.widgets:
                if widget.enabled:
                    widget.disable()
            
            # Disconnect the events relating to non-content communication
            self.event_dispatch.unregister('persist_preferences', self.persist_widgets_preferences)
            self.event_dispatch.unregister('hide_context_menu', self.hide_context_menu)
            self.event_dispatch.unregister('deactivate_poller', self.deactivate_poller)
            self.event_dispatch.unregister('show_context_menu', self.move_context_menu)      
            
            self.disable_poller_job = cron.interval('30ms', self.disable_poller_check)
            self.display_state.unregister('content_update', self.content_update)
            self.display_state.unregister('panel_update', self.panel_update)
            self.display_state.unregister('log_update', self.log_update)
            self.display_state.unregister('log_revise', self.log_revise)
            ui.unregister('screen_change', self.reload_preferences)
            settings.unregister("user.talon_hud_environment", self.hud_environment_change)            
            self.determine_active_setup_mouse()
            
            if persisted:
                self.preferences.persist_preferences({'enabled': False})
                
            # Make sure context isn't updated in this thread because of automatic reloads
            cron.cancel(self.update_context_debouncer)
            self.update_context_debouncer = cron.after("50ms", self.update_context)
            
    # Persist the preferences of all the widgets
    def persist_widgets_preferences(self, _ = None):
        dict = {}
        for widget in self.widget_manager.widgets:
            if widget.preferences.mark_changed:
                dict = {**dict, **widget.preferences.export(widget.id)}
                widget.preferences.mark_changed = False
        self.preferences.persist_preferences(dict)
        self.determine_active_setup_mouse()        
    
    def enable_id(self, id):
        if not self.enabled:
            self.enable()
    
        for widget in self.widget_manager.widgets:
            if not widget.enabled and widget.id == id:
                widget.enable(True)
                if widget.topic in self.pollers and not self.pollers[widget.topic].enabled:
                	self.pollers[widget.topic].enable()

                if isinstance(widget, HeadUpTextPanel) and widget.panel_content.tags != None \
                   and len(widget.panel_content.tags) > 0:
                   self.update_context()

    def disable_id(self, id):
        for widget in self.widget_manager.widgets:
            if widget.enabled and widget.id == id:
                widget.disable(True)
                if widget.topic in self.pollers and widget.topic not in self.keep_alive_pollers:
                	self.pollers[widget.topic].disable()
                    
                if isinstance(widget, HeadUpTextPanel) and widget.panel_content.tags != None \
                   and len(widget.panel_content.tags) > 0:
                   self.update_context()
        self.determine_active_setup_mouse()
        
    def subscribe_content_id(self, id, content_key):
        for widget in self.widget_manager.widgets:
            if widget.id == id:
                if content_key not in widget.subscribed_content:
                    widget.subscribed_content.append(content_key)
                    
    def unsubscribe_content_id(self, id, content_key):
        for widget in self.widget_manager.widgets:
            if widget.id == id:
                if content_key in widget.subscribed_content:
                    widget.subscribed_content.remove(content_key)

    def set_widget_preference(self, id, property, value, persisted=False):
        for widget in self.widget_manager.widgets:
            if widget.id == id:
                widget.set_preference(property, value, persisted)
        self.determine_active_setup_mouse()

    def switch_theme(self, theme_name, disable_animation = False):
        if (self.theme.name != theme_name):
            self.theme = HeadUpDisplayTheme(theme_name)
            for widget in self.widget_manager.widgets:
                if disable_animation:
                    show_animations = widget.show_animations
                    widget.show_animations = False
                    widget.set_theme(self.theme)
                    widget.show_animations = show_animations
                else:
                    widget.set_theme(self.theme)
            
            self.preferences.persist_preferences({'theme_name': theme_name})

    def start_setup_id(self, id, setup_type, mouse_pos = None):
        for widget in self.widget_manager.widgets:
            if widget.enabled and ( id == "*" or widget.id == id ) and widget.setup_type != setup_type:
                widget.start_setup(setup_type, mouse_pos)
                
        self.determine_active_setup_mouse()
        
    def reload_preferences(self, _= None):
        """Reload user preferences ( in case a monitor switches or something )"""
        self.widget_manager.reload_preferences(False, self.current_talon_hud_environment)
    
    def register_poller(self, topic: str, poller: Poller, keep_alive: bool):
        self.remove_poller(topic)
        self.pollers[topic] = poller
        
        # Keep the poller alive even if no widgets have subscribed to its topic
        if keep_alive and not self.pollers[topic].enabled:
            self.keep_alive_pollers.append(topic)
            self.pollers[topic].enable()
        # Automatically enable the poller if it was active on restart        
        else:
            for widget in self.widget_manager.widgets:
                if widget.topic == topic:
                    self.pollers[topic].enable()
                    break
        
    def remove_poller(self, topic: str):
        if topic in self.pollers:
            self.pollers[topic].disable()
            del self.pollers[topic]
            
    def deactivate_poller(self, topic: str):
        if topic in self.pollers:
            self.pollers[topic].disable()
    
    def activate_poller(self, topic: str):
        # Find the widget we need to claim for this topic
        # Prioritizing specific widgets over the fallback one
        widget_to_claim = None
        using_fallback = True
        if topic not in self.keep_alive_pollers:
            for widget in self.widget_manager.widgets:
                if topic in widget.subscribed_topics or ('*' in widget.subscribed_topics and using_fallback):
                    widget_to_claim = widget
                    if topic in widget.subscribed_topics:
                   	    using_fallback = False
        
        # Deactivate the topic connected to the widget
        if widget_to_claim:
            if widget_to_claim.topic in self.pollers and widget_to_claim.topic not in self.keep_alive_pollers:
                self.pollers[widget_to_claim.topic].disable()
            widget_to_claim.set_topic(topic)
    	
    	# Enable the poller afterwards
        if topic in self.pollers and not self.pollers[topic].enabled:
            self.pollers[topic].enable()
		
         
    # Check if the widgets are finished unloading, then disable the poller
    # This should only run when we have a state poller
    def disable_poller_check(self):
        enabled = False
        for widget in self.widget_manager.widgets:
            if not widget.cleared:
                enabled = True
                break
        
        if not enabled:
            for topic, poller in self.pollers.items():
                poller.disable()
            cron.cancel(self.disable_poller_job)
            self.disable_poller_job = None
        
    def content_update(self, data):
        for widget in self.widget_manager.widgets:
            update_dict = {}
            for key in data:
                if key in widget.subscribed_content:
                    update_dict[key] = data[key]
                    
            if len(update_dict) > 0:
                current_enabled_state = widget.enabled
                widget.update_content(update_dict)
                
                # If the enabled state has changed because of a content update like a sleep command
                # Do appropriate poller enabling / disabling
                if widget.enabled != current_enabled_state:
                    if widget.topic in self.pollers and widget.topic not in self.keep_alive_pollers:
                        if widget.enabled: 
                            self.pollers[widget.topic].enable()
                        else:
                            self.pollers[widget.topic].disable()
                
    def log_update(self, logs):
        new_log = logs[-1]
        for widget in self.widget_manager.widgets:
            if new_log['type'] in widget.subscribed_logs or '*' in widget.subscribed_logs:
                widget.append_log(new_log)
                
    def log_revise(self, logs):
        for widget in self.widget_manager.widgets:
            if logs[0]['type'] in widget.subscribed_logs or '*' in widget.subscribed_logs:
                widget.revise_logs(logs)
        

    def panel_update(self, panel_content: HudPanelContent):
        updated = False
        widget_to_claim = None
        using_fallback = True
        topic = panel_content.topic
        
        # Do not force a reopen of Talon HUD without explicit user permission
        if not self.enabled:
            panel_content.show = False
        
        # Find the widget to use for updating
        # Prefer the widget that is already registered 
        # Then widgets that have a topic subscribed
        # And lastly the fallback widget
        if topic not in self.keep_alive_pollers:
            for widget in self.widget_manager.widgets:
                if topic in widget.subscribed_topics or ('*' in widget.subscribed_topics and using_fallback):
                    if topic == widget.topic:
                        widget_to_claim = widget
                        break
                    else:
                        widget_to_claim = widget
                        if topic in widget.subscribed_topics:
                            using_fallback = False

        if widget_to_claim:
			# When a new topic is published it can lay claim to a widget
            # So old pollers need to be deregistered in that case
            current_topic = widget_to_claim.topic                
            updated = widget_to_claim.update_panel(panel_content)
            if updated and current_topic != widget_to_claim.topic:
                if current_topic in self.pollers and current_topic not in self.keep_alive_pollers:
                    self.pollers[widget_to_claim.topic].disable()
            
        if updated:
            self.update_context()

    # Determine whether or not we need to have a global mouse poller
    # This poller is needed for setup modes as not all canvases block the mouse
    def determine_active_setup_mouse(self):
        has_setup_modes = False
        for widget in self.widget_manager.widgets:
            if (widget.setup_type not in ["", "mouse_drag"]):
                has_setup_modes = True
                break
    
        if has_setup_modes and not self.mouse_poller:
            self.mouse_poller = cron.interval('16ms', self.poll_mouse_pos_for_setup)
        if not has_setup_modes and self.mouse_poller:
            cron.cancel(self.mouse_poller)
            self.mouse_poller = None

    # Send mouse events to enabled widgets that have an active setup going on
    def poll_mouse_pos_for_setup(self):
        pos = ctrl.mouse_pos()
        
        if (self.prev_mouse_pos is None or numpy.linalg.norm(numpy.array(pos) - numpy.array(self.prev_mouse_pos)) > 1):
            self.prev_mouse_pos = pos
            for widget in self.widget_manager.widgets:
                if widget.enabled and widget.setup_type != "":
                    widget.setup_move(self.prev_mouse_pos)

    # Increase the page number by one on the widget if it is enabled
    def increase_widget_page(self, widget_id: str):
        for widget in self.widget_manager.widgets:
            if widget.enabled and widget.id == widget_id and isinstance(widget, LayoutWidget):
                widget.set_page_index(widget.page_index + 1)

    # Decrease the page number by one on the widget if it is enabled
    def decrease_widget_page(self, widget_id: str):
        for widget in self.widget_manager.widgets:
            if widget.enabled and widget.id == widget_id and isinstance(widget, LayoutWidget):
                widget.set_page_index(widget.page_index - 1)

    # Move the context menu over to the given location fitting within the screen
    def move_context_menu(self, widget_id: str, pos: Point2d, buttons: list[HudButton]):
        connected_widget = None
        context_menu_widget = None
        for widget in self.widget_manager.widgets:
            if widget.enabled and widget.id == widget_id:
                connected_widget = widget
            elif widget.id == 'context_menu':      
                context_menu_widget = widget
        if connected_widget and context_menu_widget:
            context_menu_widget.connect_widget(connected_widget, pos.x, pos.y, buttons)
            self.choices_visible = True
            self.update_context()
            
    # Connect the context menu using voice
    def connect_context_menu(self, widget_id):
        connected_widget = None
        context_menu_widget = None
        for widget in self.widget_manager.widgets:
            if widget.enabled and widget.id == widget_id:
                connected_widget = widget
            elif widget.id == 'context_menu':      
                context_menu_widget = widget
        
        buttons = []
        if connected_widget:
            pos_x = connected_widget.x + connected_widget.width / 2
            pos_y = connected_widget.y + connected_widget.height
            buttons = connected_widget.buttons
        
            if context_menu_widget:
                context_menu_widget.connect_widget(connected_widget, pos_x, pos_y, buttons)
                self.choices_visible = True
                self.update_context()                
    
    # Hide the context menu
    # Generally you want to do this when you click outside of the menu itself
    def hide_context_menu(self, _ = None):
        context_menu_widget = None    
        for widget in self.widget_manager.widgets:
            if widget.id == 'context_menu' and widget.enabled:      
                context_menu_widget = widget
                break
        if context_menu_widget:
            context_menu_widget.disconnect_widget()
            self.choices_visible = False
            self.update_context()
    
    # Active a given choice for a given widget
    def activate_choice(self, choice_string):
        widget_id, choice_index = choice_string.split("|")
        for widget in self.widget_manager.widgets:
            if widget.id == widget_id:
                if isinstance(widget, HeadUpChoicePanel):
                    widget.select_choice(int(choice_index))
                    self.update_context()
                else:
                    widget.click_button(int(choice_index))
                    self.update_context()

    # Updates the context based on the current HUD state
    # This needs to be done on user actions - Automatic flows need higher scrutiny
    def update_context(self):
        tags = [
            'user.talon_hud_available'
        ]
        
        widget_names = {}
        choices = {}
        quick_choices = {}
        numerical_choices = {}
        themes = {}
        
        themes_directory = os.path.dirname(os.path.abspath(__file__)) + "/themes"
        themes_list = os.listdir(themes_directory)
        for theme in themes_list:
            themes[string_to_speakable_string(theme)] = theme
        
        for widget in self.widget_manager.widgets:
            current_widget_names = [string_to_speakable_string(widget.id)]        
            if isinstance(widget, HeadUpTextPanel):
                content_title = string_to_speakable_string(widget.panel_content.title)
                if content_title:
                    current_widget_names.append(string_to_speakable_string(widget.panel_content.title))
                    
            for widget_name in current_widget_names:
                widget_names[widget_name] = widget.id
                
            # Add quick choices
            for index, button in enumerate(widget.buttons):
                choice_title = string_to_speakable_string(button.text)                    
                if choice_title:
                    for widget_name in current_widget_names:
                        quick_choices[widget_name + " " + choice_title] = widget.id + "|" + str(index)

            # Add tags set for specific content on display
            if widget.enabled and isinstance(widget, HeadUpTextPanel) and widget.panel_content.tags is not None:
                for index, tag in enumerate(widget.panel_content.tags):
                    tags.append(tag)
            
            # Add context choices
            if widget.enabled and isinstance(widget, HeadUpContextMenu):
                for index, button in enumerate(widget.buttons):
                    choice_title = string_to_speakable_string(button.text)
                    if choice_title:
                        choices[choice_title] = widget.id + "|" + str(index)
             
            # Add choice panel choices
            if widget.enabled and isinstance(widget, HeadUpChoicePanel):
                self.choices_visible = True
                for index, choice in enumerate(widget.choices):
                    choice_title = string_to_speakable_string(choice.text)
                    if choice_title:
                        choices[choice_title] = widget.id + "|" + str(index)
                    numerical_choices[numerical_choice_strings[index]] = widget.id + "|" + str(index)
                        
                if widget.panel_content.choices and widget.panel_content.choices.multiple:
                    choices["confirm"] = widget.id + "|" + str(index + 1)

        if self.enabled:
            tags.append('user.talon_hud_visible')
        if self.choices_visible:
            tags.append('user.talon_hud_choices_visible')
        ctx.tags = tags
        ctx.lists['user.talon_hud_numerical_choices'] = numerical_choices
        ctx.lists['user.talon_hud_widget_names'] = widget_names
        ctx.lists['user.talon_hud_choices'] = choices
        ctx.lists['user.talon_hud_quick_choices'] = quick_choices
        ctx.lists['user.talon_hud_themes'] = themes

    def hud_environment_change(self, hud_environment: str):
        if self.current_talon_hud_environment != hud_environment:
            self.current_talon_hud_environment = hud_environment
            
            # Add a debouncer for the environment change to reduce flickering on transitioning
            cron.cancel(self.update_environment_debouncer)
            self.update_environment_debouncer = cron.after("200ms", self.debounce_environment_change)

    def debounce_environment_change(self):
        reload_theme = self.widget_manager.reload_preferences(True, self.current_talon_hud_environment)
        
        # Switch the theme and make sure there is no lengthy animation between modes 
        # as they can happen quite frequently
        self.switch_theme(reload_theme, True)        

preferences = HeadUpDisplayUserPreferences() 
hud = HeadUpDisplay(hud_content, preferences)

def hud_start():
    global hud
    hud.start()

app.register('ready', hud_start)

@mod.action_class
class Actions:
                
    def enable_hud():
        """Enables the HUD"""
        global hud
        hud.enable(True)

    def disable_hud():
        """Disables the HUD"""
        global hud
        hud.disable(True)

    def persist_hud_preferences():
        """Saves the HUDs preferences"""
        global hud
        hud.persist_widgets_preferences()

    def enable_hud_id(id: str):
        """Enables a specific HUD element"""
        global hud        
        hud.enable_id(id)
        
    def set_widget_preference(id: str, property: str, value: Any):
        """Set a specific widget preference"""
        global hud        
        hud.set_widget_preference(id, property, value, True)        
        
    def hud_widget_subscribe_topic(id: str, topic: str):
        """Subscribe to a specific type of content on a widget"""
        global hud
        hud.subscribe_content_id(id, topic)
        
    def hud_widget_unsubscribe_topic(id: str, topic: str):
        """Unsubscribe from a specific type of content on a widget"""
        global hud
        hud.unsubscribe_content_id(id, topic)
        
    def disable_hud_id(id: str):
        """Disables a specific HUD element"""
        global hud
        hud.disable_id(id)
        
    def switch_hud_theme(theme_name: str):
        """Switches the UI theme"""
        global hud
        hud.switch_theme(theme_name)
        
    def set_hud_setup_mode(id: str, setup_mode: str):
        """Starts a setup mode which can change position"""
        global hud
        hud.start_setup_id(id, setup_mode)

    def set_hud_setup_mode_multi(ids: list[str], setup_mode: str):
        """Starts a setup mode which can change position for multiple widgets at the same time"""
        global hud
        
        # In case we are dealing with drag, we can allow multiple widgets to be dragged at the same time
        mouse_pos = None
        if (len(ids) > 1 and setup_mode == "position"):
            mouse_pos = ctrl.mouse_pos()
        
        for id in ids:
            hud.start_setup_id(id, setup_mode, mouse_pos)
                
    def show_context_menu(widget_id: str, pos_x: int, pos_y: int, buttons: list[HudButton]):
        """Show the context menu for a specific widget id"""
        hud.move_context_menu(widget_id, Point2d(pos_x, pos_y), buttons)
        
    def hide_context_menu():
        """Show the context menu for a specific widget id"""
        hud.hide_context_menu()
        
    def increase_widget_page(widget_id: str):
        """Increase the content page of the widget if it has pages available"""
        global hud
        hud.increase_widget_page(widget_id)

    def decrease_widget_page(widget_id: str):
        """Decrease the content page of the widget if it has pages available"""
        global hud
        hud.decrease_widget_page(widget_id)
        
    def decrease_widget_page(widget_id: str):
        """Decrease the content page of the widget if it has pages available"""
        global hud
        hud.decrease_widget_page(widget_id)
        
    def hud_widget_options(widget_id: str):
        """Connect the widget to the context menu to show the options"""
        global hud
        hud.connect_context_menu(widget_id)
        
    def hud_activate_choice(choice_string: str):
        """Activate a choice available on the screen"""    
        global hud
        hud.activate_choice(choice_string)
        
    def hud_activate_choices(choice_string_list: list[str]):
        """Activate multiple choices available on the screen"""    
        global hud
        for choice_string in choice_string_list:
            hud.activate_choice(choice_string)
        
    def hud_add_poller(topic: str, poller: Poller, keep_alive: bool = False):
        """Add a content poller / listener to the HUD"""    
        global hud
        hud.register_poller(topic, poller, keep_alive)
        
    def hud_remove_poller(topic: str):
        """Remove a content poller / listener to the HUD"""    
        global hud
        hud.remove_poller(topic)
        
    def hud_activate_poller(topic: str):
        """Enables a poller and claims a widget"""    
        global hud
        hud.activate_poller(topic)
        
    def hud_deactivate_poller(topic: str):
        """Disables a poller"""    
        global hud
        hud.deactivate_poller(topic)
        
    def hud_get_theme() -> HeadUpDisplayTheme:
        """Get the current theme object from the HUD"""
        global hud
        return hud.theme