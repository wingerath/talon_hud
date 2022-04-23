from ..base_widget import BaseWidget
from ..utils import layout_rich_text, hit_test_rect, is_light_colour, hex_to_ints
from ..content.typing import HudScreenRegion
from ..widget_preferences import HeadUpDisplayUserWidgetPreferences
from talon import skia, ui, cron, ctrl, canvas
from talon.types.point import Point2d
import time
import numpy

class HeadUpScreenOverlay(BaseWidget):

    # In this widget the limit_width and limit_height variables are used to determine the size of the canvas

    allowed_setup_options = ["dimension", "font_size"]
    mouse_enabled = False
    soft_enabled = False
    mouse_poller = None
    prev_mouse_pos = None
    smooth_mode = True

    preferences = HeadUpDisplayUserWidgetPreferences(type="screen_overlay", x=0, y=0, width=300, height=30, font_size=12, enabled=True, alignment="center", expand_direction="down", sleep_enabled=False)
    
    # New content topic types
    topic_types = ["screen_regions"]
    current_topics = []
    subscriptions = ["*"]
    
    regions = None
    active_regions = None
    canvases = None
    
    def __init__(self, id, preferences_dict, theme, event_dispatch, subscriptions = None, current_topics = None):
        super().__init__(id, preferences_dict, theme, event_dispatch, subscriptions, current_topics)
        self.regions = []
        self.active_regions = []
        self.canvases = []
    
    def refresh(self, new_content):
        if "event" in new_content and new_content["event"].topic_type == "screen_regions":
            self.update_regions()
        elif "event" in new_content and new_content["event"].topic_type == "variable" and new_content["event"].topic == "mode":
            if (new_content["event"].content == "sleep" and self.sleep_enabled == False):
                self.soft_disable()
            else:
                self.soft_enable()

    def enable(self, persisted=False):
        if not self.enabled:
            self.enabled = True        
            self.previous_pos = ctrl.mouse_pos()
            
            # Copied over from base widget and altered to reflect the no-canvas state of this widget            
            self.canvas = None
            if persisted:
                self.preferences.enabled = True
                self.preferences.mark_changed = True
                self.event_dispatch.request_persist_preferences()
            
            self.cleared = False
            self.soft_enable()
            self.create_canvases()
    
    def disable(self, persisted=False):
        if self.enabled:
            self.soft_disable()
            
            # Copied over from base widget and altered to reflect the no-canvas state of this widget
            self.enabled = False
            self.animation_tick = -self.animation_max_duration if self.show_animations else 0
            
            if persisted:
                self.preferences.enabled = False
                self.preferences.mark_changed = True
                self.event_dispatch.request_persist_preferences()
            
            self.start_setup("cancel")
            self.clear()
    
    def soft_enable(self):
        if not self.soft_enabled:
            self.soft_enabled = True
            self.activate_mouse_tracking()

    def soft_disable(self):
        self.clear_canvases()
        if self.soft_enabled:
            self.soft_enabled = False
            cron.cancel(self.mouse_poller)
            self.mouse_poller = None
            self.regions = []
            self.active_regions = []

    def update_regions(self):
        self.active_regions = []
        if not self.enabled:
            self.regions = self.content.get_topic("screen_regions")
            return
        
        soft_enable = False
        indices_to_clear = []
        region_indices_used = []
        regions = self.content.get_topic("screen_regions")
        
        if regions is not None:
            new_regions = regions
            for index, canvas_reference in enumerate(self.canvases):
                region_found = False
                for region_index, region in enumerate(new_regions):
                
                    # Move the canvas in case we are dealing with the same region
                    if self.compare_regions(canvas_reference["region"], region):
                        region_found = True
                        region_indices_used.append(region_index)
                        canvas_reference["canvas"].unregister("draw", canvas_reference["callback"])
                        
                        canvas_rect = self.align_region_canvas_rect(region)
                        canvas_reference["canvas"].move(canvas_rect.x, canvas_rect.y)
                        canvas_reference["callback"] = lambda canvas, self=self, region=region: self.draw_region(canvas, region)
                        canvas_reference["region"] = region
                        canvas_reference["canvas"].register("draw", canvas_reference["callback"])
                        canvas_reference["canvas"].freeze()
                        self.canvases[index] = canvas_reference
                
                if region_found == False:
                    indices_to_clear.append(index)
                    canvas_reference["canvas"].unregister("draw", canvas_reference["callback"])
                    canvas_reference["region"] = None
                    canvas_reference["canvas"] = None
                    canvas_reference = None
            
            soft_enable = ( self.regions != new_regions or not self.soft_enabled ) and len(new_regions) > 0
            self.regions = new_regions
        
        # Clear the canvases in reverse order to make sure the indices stay correct
        indices_to_clear.reverse()
        for index in indices_to_clear:
            self.canvases.pop(index)
        
        if len(self.regions) > 0:
            if soft_enable:
                self.soft_enable()
            self.activate_mouse_tracking()
            self.create_canvases(region_indices_used)
            self.determine_active_regions(ctrl.mouse_pos())
        else:
            self.soft_disable()

    def create_canvases(self, region_indices_used = None):
        if not region_indices_used:
            region_indices_used = []
    
        for index, region in enumerate(self.regions):
            if index not in region_indices_used:
                canvas_rect = self.align_region_canvas_rect(region)
                canvas_reference = {"canvas": canvas.Canvas(canvas_rect.x, canvas_rect.y, canvas_rect.width, canvas_rect.height)}
                canvas_reference["callback"] = lambda canvas, self=self, region=region: self.draw_region(canvas, region)
                canvas_reference["region"] = region
                canvas_reference["canvas"].register("draw", canvas_reference["callback"])
                canvas_reference["canvas"].freeze()
                self.canvases.append(canvas_reference)
            
    def clear_canvases(self):
        for canvas_reference in self.canvases:
            if canvas_reference:
                canvas_reference["canvas"].unregister("draw", canvas_reference["callback"])
                canvas_reference["region"] = None
                canvas_reference["canvas"] = None
                canvas_reference = None
        self.canvases = []
        
    def align_region_canvas_rect(self, region):
        if region.rect:
            horizontal_margin = self.theme.get_int_value("screen_overlay_region_horizontal_margin", 10)
            vertical_margin = self.theme.get_int_value("screen_overlay_region_vertical_margin", 2)
        
            y = region.rect.y    
            if self.expand_direction == "up":
                y += region.rect.height - self.height - vertical_margin
            else:
                y += vertical_margin
                
            x = region.rect.x
            if self.alignment == "right":
                x = region.rect.x + region.rect.width - self.limit_width - horizontal_margin
            elif self.alignment == "center":
                x = region.rect.x + ( region.rect.width - self.limit_width ) / 2
            else:
                x += horizontal_margin
            
            if region.vertical_centered:
                y = region.rect.y + (region.rect.height - self.height) / 2
                
                            
            return ui.Rect(x, y, self.limit_width, self.limit_height)
        else:
            return ui.Rect(x, y, 0, 0)
    
    def compare_regions(self, region_a, region_b):
        return region_a.topic == region_b.topic and region_a.colour == region_b.colour and (
            region_a.icon == region_b.icon or region_a.title == region_b.title )
    
    def activate_mouse_tracking(self):
        has_active_region = len([x for x in self.regions if x.hover_visibility]) > 0
        if not self.mouse_poller and has_active_region:
            self.mouse_poller = cron.interval("30ms", self.poll_mouse_pos)
        elif not has_active_region:
            self.active_regions = self.regions
            for canvas_reference in self.canvases:
                canvas_reference["canvas"].freeze()
    
    def poll_mouse_pos(self):
        if self.enabled:
            pos = ctrl.mouse_pos()
            distance_threshold = 0.5 if self.smooth_mode else 20
            if (self.prev_mouse_pos is None or numpy.linalg.norm(numpy.array(pos) - numpy.array(self.prev_mouse_pos)) > distance_threshold):
                self.prev_mouse_pos = pos
                self.determine_active_regions(pos)
    
    # Determine the active regions based on the region the icon is in
    def determine_active_regions(self, pos):
        pos = Point2d(pos[0], pos[1])
        active_regions = []
        size = None
        for region in self.regions:
            if not region.hover_visibility:
                active_regions.append(region)
            else:
                if region.rect is None:
                    active_regions.append(region)
                elif region.rect is not None and region.hover_visibility == 1 and hit_test_rect(region.rect, pos):
                    active_regions.append(region)
                # For hover visibility -1 , we want the region canvas to be translucent when hovered
                # So it doesn"t occlude content - But otherwise it should be visible
                elif region.hover_visibility == -1:
                    rect = self.align_region_canvas_rect(region)
                    if not hit_test_rect(rect, pos):
                        active_regions.append(region)
                        
        if self.active_regions != active_regions:
            self.active_regions = active_regions
            for canvas_reference in self.canvases:
                canvas_reference["canvas"].freeze()
            
    def draw_region(self, canvas, region, setup_region = False) -> bool:
        paint = self.draw_setup_mode(canvas)
        paint.textsize = self.font_size        
        if self.soft_enabled:
            active = setup_region or region in self.active_regions
            
            background_colour = region.colour if active else self.theme.get_colour("screen_overlay_background_colour", "F5F5F588")
            paint.color = background_colour if background_colour else self.theme.get_colour("screen_overlay_active_background_colour", "F5F5F5")
            
            vertical_padding = self.theme.get_int_value("screen_overlay_vertical_padding", 4)
            horizontal_padding = self.theme.get_int_value("screen_overlay_horizontal_padding", 4)
            icon_size = self.height if region.icon or region.colour and not region.title else 0
            text_width = 0

            # Do a small layout pass
            canvas_rect = self.align_region_canvas_rect(region)            
            content_text = []
            if region.title:
                content_text = layout_rich_text(paint, region.title, self.limit_width - icon_size, self.limit_height)
                for index, content in enumerate(content_text):
                    if index != 0 and content.x == 0:
                        break
                    else:
                        text_width += content.width
            
            icon_x = canvas_rect.x
            if self.alignment == "center":
                icon_x += ( canvas_rect.width - icon_size - text_width ) / 2
            elif self.alignment == "right":
                icon_x += canvas_rect.width - icon_size - text_width - horizontal_padding
            else:
                icon_x += horizontal_padding
            text_y = canvas_rect.y + ( icon_size - self.font_size ) / 2 if region.icon else canvas_rect.y + self.font_size - vertical_padding
            
            text_x = icon_x + icon_size

            # First draw the text background
            if region.title:
                background_width = min(self.limit_width - icon_size, text_width)
                background_rect = ui.Rect(text_x - self.font_size / 2 - horizontal_padding, \
                    text_y - vertical_padding, \
                    background_width + horizontal_padding * 2 + self.font_size / 2,
                    self.font_size + vertical_padding * 2)
                
                # We do not need to hide behind an icon, so no extra padding is needed
                if not icon_size:
                    background_rect.x += horizontal_padding
                    background_rect.width -= horizontal_padding
                    
                rrect = skia.RoundRect.from_rect(background_rect, x=self.font_size / 2, y=self.font_size / 2)
                canvas.draw_rrect(rrect)
            
            # Then draw the icon size
            if icon_size:
                self.draw_icon(canvas, icon_x, canvas_rect.y, icon_size, paint, region, active)
            
            # Finally draw the text on top
            if region.title:
                text_colour = region.text_colour if active else self.theme.get_colour("screen_overlay_text_colour", "00000044")
                if not text_colour:
                    text_colour = self.theme.get_colour("screen_overlay_text_colour", "00000044") if not active else self.theme.get_colour("screen_overlay_active_text_colour", "000000FF")                    
                
                # Draw the background colour of the text
                text_colour_ints = hex_to_ints(text_colour)
                text_background_colour = "000000" if is_light_colour(text_colour_ints[0], text_colour_ints[1], text_colour_ints[2]) else "FFFFFF"
                if len(text_colour_ints) > 3:
                    opacity_hex = format(text_colour_ints[3], "x")
                    opacity_hex = opacity_hex if len(opacity_hex) > 1 else "0" + opacity_hex
                    text_background_colour += opacity_hex
                paint.color = text_background_colour                
                self.draw_rich_text(canvas, paint, content_text, text_x, text_y + 1, 0, True)
                
                paint.color = text_colour
                self.draw_rich_text(canvas, paint, content_text, text_x, text_y, 0, True)            
                
        
    def draw_icon(self, canvas, origin_x, origin_y, diameter, paint, region, active):
        radius = diameter / 2
        
        if region.colour is not None or ( region.title is not None and region.icon is not None ):
            canvas.draw_circle( origin_x + radius, origin_y + radius, radius, paint)
        
        if (region.icon is not None and self.theme.get_image(region.icon) is not None ):
            icon_border = self.theme.get_int_value("screen_overlay_icon_padding", 4)
            image = self.theme.get_image(region.icon, diameter - icon_border, diameter - icon_border)
            canvas.draw_image(image, origin_x + radius - ( image.height ) / 2, \
                origin_y + radius - ( image.height ) / 2 )

    def draw_rich_text(self, canvas, paint, rich_text, x, y, line_padding, single_line=False):
        # Draw text line by line
        text_colour = paint.color
        error_colour = self.theme.get_colour("error_colour", "AA0000")
        warning_colour = self.theme.get_colour("warning_colour", "F75B00")
        success_colour = self.theme.get_colour("success_colour", "00CC00")
        info_colour = self.theme.get_colour("info_colour", "30AD9E")
    
        current_line = -1
        for index, text in enumerate(rich_text):
            paint.font.embolden = "bold" in text.styles
            paint.font.skew_x = -0.33 if "italic" in text.styles else 0
            paint.color = text_colour
            if "warning" in text.styles:
                paint.color = warning_colour
            elif "success" in text.styles:
                paint.color = success_colour
            elif "error" in text.styles:
                paint.color = error_colour
            elif "notice" in text.styles:
                paint.color = info_colour
                        
            current_line = current_line + 1 if text.x == 0 else current_line
            if single_line and current_line > 0:
                return
            
            if text.x == 0:
                y += paint.textsize
                if index != 0:
                    y += line_padding
            
            canvas.draw_text(text.text, x + text.x, y )


    def start_setup(self, setup_type, mouse_position = None):
        """Starts a setup mode that is used for moving, resizing and other various changes that the user might setup"""    
        if (mouse_position is not None):
            self.drag_position = [mouse_position[0] - self.limit_x, mouse_position[1] - self.limit_y]
        
        if (setup_type not in self.allowed_setup_options and setup_type not in ["", "cancel", "reload"] ):
            return
            
        pos = ctrl.mouse_pos()
        
        # Persist the user preferences when we end our setup
        if (self.setup_type != "" and not setup_type):
            self.drag_position = []
            rect = self.canvas.rect
            
            if (self.setup_type == "dimension"):
                self.x = 0
                self.y = 0
                self.width = int(rect.width)
                self.height = int(rect.height)
                self.limit_width = int(rect.width)
                self.limit_height = int(rect.height)
                self.preferences.width = self.limit_width
                self.preferences.height = self.limit_height
                self.preferences.limit_width = self.limit_width
                self.preferences.limit_height = self.limit_height
            elif (self.setup_type == "font_size" ):
                self.preferences.font_size = self.font_size

            self.setup_type = setup_type
            self.preferences.mark_changed = True
            self.canvas.pause()
            self.canvas.unregister("draw", self.setup_draw_cycle)
            self.canvas = None
            
            self.event_dispatch.request_persist_preferences()
        # Cancel every change
        elif setup_type == "cancel":
            self.drag_position = []        
            if (self.setup_type != ""):
                self.load({}, False)
                
                self.setup_type = ""                
                if self.canvas:
                    self.canvas.unregister("draw", self.setup_draw_cycle)
                    self.canvas = None

                for canvas_reference in self.canvases:
                    canvas_rect = self.align_region_canvas_rect(canvas_reference["region"])
                    canvas_reference["canvas"].rect = canvas_rect
                    canvas_reference["canvas"].freeze()
                    
        elif setup_type == "reload":
            self.drag_position = []  
            self.setup_type = ""
            for canvas_reference in self.canvases:
                canvas_reference["canvas"].freeze()
                
        # Start the setup by mocking a full screen screen region to place the canvas in
        else:
            main_screen = ui.main_screen()
            region = HudScreenRegion("setup", "Setup mode text", "command_icon", "DD4500", main_screen.rect, \
                Point2d(main_screen.rect.x, main_screen.rect.y))
            region.vertical_centered = True
            canvas_rect = self.align_region_canvas_rect(region)
            self.x = canvas_rect.x
            self.y = canvas_rect.y
            
            if not self.canvas:
                self.canvas = canvas.Canvas(self.x, self.y, self.limit_width, self.limit_height)
                self.canvas.register("draw", self.setup_draw_cycle)            
            self.canvas.move(self.x, self.y)
            self.canvas.resume()
            super().start_setup(setup_type, mouse_position)
                
    def setup_move(self, pos):
        """Responds to global mouse movements when a widget is in a setup mode"""
        if (self.setup_type == "position"):
            pass
        elif (self.setup_type in ["dimension", "limit", "font_size"] ):
            super().setup_move(pos)
            
            for canvas_reference in self.canvases:
                canvas_rect = self.align_region_canvas_rect(canvas_reference["region"])
                canvas_reference["canvas"].rect = canvas_rect            
                canvas_reference["canvas"].freeze()
            

    def setup_draw_cycle(self, canvas):
        """Drawing cycle that mimics a screen region set up"""
        region = HudScreenRegion("setup", "Setup mode text", "command_icon", "DD4500", canvas.rect, \
            Point2d(canvas.rect.x, canvas.rect.y), vertical_centered = True)
        region.text_colour = "FFFFFF"
        canvas_rect = self.align_region_canvas_rect(region)
        self.draw_region(canvas, region, True)
        if self.canvas:
            self.canvas.pause()

    def set_preference(self, preference, value, persisted=False):
        # Copied over from base widget to reflect the no-canvas state of this widget
        dict = {}
        dict[self.id + "_" + preference] = value
        self.load(dict, False)
        if self.enabled:
            for canvas_reference in self.canvases:
                canvas_rect = self.align_region_canvas_rect(canvas_reference["region"])
                canvas_reference["canvas"].move(canvas_rect.x, canvas_rect.y)            
                canvas_reference["canvas"].freeze()
        
        if persisted:
            self.preferences.mark_changed = True
            self.event_dispatch.request_persist_preferences()

    def set_theme(self, theme):
        # Copied over from base widget to reflect the no-canvas state of this widget    
        self.theme = theme
        self.load_theme_values()
        if self.enabled:
            if self.canvas:
                self.canvas.freeze()
            self.animation_tick = self.animation_max_duration if self.show_animations else 0
            for canvas_reference in self.canvases:
                canvas_reference["canvas"].freeze()
            
