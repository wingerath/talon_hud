from talon import skia, ui, cron, actions, clip
from user.talon_hud.layout_widget import LayoutWidget
from user.talon_hud.widget_preferences import HeadUpDisplayUserWidgetPreferences
from user.talon_hud.utils import layout_rich_text, remove_tokens_from_rich_text, linear_gradient, retrieve_available_voice_commands, hex_to_ints, string_to_speakable_string, hit_test_icon
from user.talon_hud.content.typing import HudRichTextLine, HudPanelContent, HudButton, HudIcon
from talon.types.point import Point2d
from talon.skia import Paint

icon_radius = 9
def close_widget(widget):
    widget.disable(True)

class HeadUpWalkThroughPanel(LayoutWidget):
    preferences = HeadUpDisplayUserWidgetPreferences(type="walk_through", x=910, y=1000, width=100, height=20, limit_x=480, limit_y=826, limit_width=960, limit_height=148, enabled=False, sleep_enabled=True, alignment="center", expand_direction="up", font_size=24)
    mouse_enabled = True
    step_scheduled = None

    # Top, right, bottom, left, same order as CSS padding
    padding = [10, 20, 10, 8]
    line_padding = 6

    # Animation frame related variables
    animation_max_duration = 30
    max_transition_animation_state = 30
    max_animated_word_state = 20
    transition_animation_state = 0
    animated_word_state = 0
    animated_words = []
    commands_positions = {}
    
    icon_hovered = -1
    icons = [
        HudIcon("close", "", Point2d(0,0), icon_radius, close_widget)
    ]

    # Previous dimensions to transition from
    previous_content_dimensions = None    
    
    # Options given to the context menu
    buttons = [
        HudButton("next_icon", "Skip this step", ui.Rect(0,0,0,0), lambda widget: actions.user.hud_skip_walkthrough_step()),
        HudButton("check_icon", "Mark as done", ui.Rect(0,0,0,0), lambda widget: actions.user.hud_skip_walkthrough_all()),
        HudButton("", "Restore current step", ui.Rect(0,0,0,0), lambda widget: actions.user.hud_restore_walkthrough_step())        
    ]

    subscribed_content = ["mode", "walkthrough_said_voice_commands"]
    content = {
        'mode': 'command',
        'walkthrough_said_voice_commands': []
    }
    
    subscribed_topics = ['walk_through']
    topic = 'walk_through'
    
    panel_content = HudPanelContent('walk_through', '', [''], [], 0, False)
    voice_commands_available = []
    
    def disable(self, persisted=False):
       self.previous_content_dimensions = None
       self.transition_animation_state = 0
       self.event_dispatch.deactivate_poller('walk_through')
       super().disable(persisted)

    def refresh(self, new_content):
        # Animate the new words
        if "walkthrough_said_voice_commands" in new_content:
            if self.show_animations and len(new_content["walkthrough_said_voice_commands"]) > 0 and \
                new_content["walkthrough_said_voice_commands"] != self.content["walkthrough_said_voice_commands"]:
                self.animated_words = list(set(new_content["walkthrough_said_voice_commands"]) - set(self.content["walkthrough_said_voice_commands"]))
                self.animated_word_state = self.max_animated_word_state
            elif len(new_content["walkthrough_said_voice_commands"]) == 0:
                self.animated_words = []
                self.animated_word_state = 0

        # Navigate to the next step if all our voice commands have been exhausted
        if len(self.voice_commands_available) > 0 and "walkthrough_said_voice_commands" in new_content and \
            len(self.voice_commands_available) == len(new_content["walkthrough_said_voice_commands"]):
            if not "skip step" in self.voice_commands_available:
                cron.cancel(self.step_scheduled)
                self.step_scheduled = cron.after(str(self.theme.get_int_value("walkthrough_panel_step_delay_ms", 1500)) + 'ms', actions.user.hud_skip_walkthrough_step)

        super().refresh(new_content)

    def update_panel(self, panel_content) -> bool:    
        # Animate the transition
        if self.show_animations:
            should_animate = self.previous_content_dimensions is not None and self.enabled == True
            self.previous_content_dimensions = self.layout[self.page_index]['rect'] if self.page_index < len(self.layout) else None
            self.transition_animation_state = self.max_transition_animation_state if should_animate else 0
            self.animated_words = []
            
        return super().update_panel(panel_content)
    
    def on_mouse(self, event):
        icon_hovered = -1
        for index, icon in enumerate(self.icons):
            if hit_test_icon(icon, event.gpos):
                icon_hovered = index

        if event.event == "mouseup" and event.button == 0:
            clicked_icon = None
            if icon_hovered != -1:
                clicked_icon = self.icons[icon_hovered]
                
            if clicked_icon != None:
                self.icon_hovered = -1
                clicked_icon.callback(self)
            self.event_dispatch.hide_context_menu()
        elif event.button == 1 and event.event == "mouseup":            
            self.event_dispatch.show_context_menu(self.id, event.gpos, self.buttons)
        
        if icon_hovered != self.icon_hovered:
            self.icon_hovered = icon_hovered
            self.canvas.resume()
        
        # Allow dragging and dropping with the mouse
        if icon_hovered == -1:
            super().on_mouse(event)

    def set_preference(self, preference, value, persisted=False):
        self.mark_layout_invalid = True
        super().set_preference(preference, value, persisted)
        
    def load_theme_values(self):
        self.intro_animation_start_colour = self.theme.get_colour_as_ints('intro_animation_start_colour')
        self.intro_animation_end_colour = self.theme.get_colour_as_ints('intro_animation_end_colour')
        self.blink_difference = [
            self.intro_animation_end_colour[0] - self.intro_animation_start_colour[0],
            self.intro_animation_end_colour[1] - self.intro_animation_start_colour[1],
            self.intro_animation_end_colour[2] - self.intro_animation_start_colour[2]        
        ]

    def layout_content(self, canvas, paint):
        paint.textsize = self.font_size
        self.line_padding = int(self.font_size / 2) + 1 if self.font_size <= 17 else 5
        
        horizontal_alignment = "right" if self.limit_x < self.x else "left"
        vertical_alignment = "bottom" if self.limit_y < self.y else "top"
        if self.alignment == "center" or \
            ( self.x + self.width < self.limit_x + self.limit_width and self.limit_x < self.x ):
            horizontal_alignment = "center"
    
        layout_width = max(self.width - self.padding[1] * 2 - self.padding[3] * 2, 
            self.limit_width - self.padding[1] * 2 - self.padding[3] * 2)

        icon_padding = icon_radius
        content_text = [] if self.minimized else layout_rich_text(paint, self.panel_content.content[0], layout_width - icon_padding, self.limit_height)
        
        # Start segmenting the available voice commands by indexes
        self.voice_commands_available = []
        self.commands_positions = {}
        voice_command_words = []
        voice_command_indecis = []
        for index, text in enumerate(content_text):
            if "command_available" in text.styles:
                voice_command_words += string_to_speakable_string(text.text).split()
                voice_command_indecis.append(index)
            elif len(voice_command_words) > 0:
                voice_command = " ".join(voice_command_words)
                self.voice_commands_available.append(voice_command)
                for voice_command_index in voice_command_indecis:
                    self.commands_positions[str(voice_command_index)] = voice_command
                voice_command_words = []
                voice_command_indecis = []

        # Add remaining voice commands if they haven't been added yet
        if len(voice_command_words) > 0:
            voice_command = " ".join(voice_command_words)
            self.voice_commands_available.append(voice_command)
            for voice_command_index in voice_command_indecis:
                self.commands_positions[str(voice_command_index)] = voice_command
                voice_command_words = []
                voice_command_indecis = []

        layout_pages = []
        
        line_count = 0
        total_text_width = 0
        total_text_height = 0
        current_line_length = 0        
        page_height_limit = self.limit_height
        
        # We do not render content if the text box is minimized
        current_content_height = self.padding[0] + self.padding[2]
        current_page_text = []
        current_line_height = 0
        if not self.minimized:
            line_count = 0
            for index, text in enumerate(content_text):
                line_count = line_count + 1 if text.x == 0 else line_count
                current_line_length = current_line_length + text.width if text.x != 0 else text.width
                total_text_width = max( total_text_width, current_line_length )
                total_text_height = total_text_height + max(text.height, self.font_size) + self.line_padding if text.x == 0 else total_text_height
                
                current_content_height = total_text_height
                current_line_height = max(text.height, self.font_size) + self.line_padding
                if page_height_limit > current_content_height:
                    current_page_text.append(text)
                    
                # We have exceeded the page height limit, append the layout and try again
                else:
                    width = min( self.limit_width, max(self.width, total_text_width + self.padding[1] + self.padding[3]))
                    height = self.limit_height
                    x = self.x if horizontal_alignment == "left" else self.limit_x + self.limit_width - width
                    if horizontal_alignment == "center":
                        x = self.limit_x + ( self.limit_width - width ) / 2
                    y = self.limit_y if vertical_alignment == "top" else self.limit_y + self.limit_height - height
                    layout_pages.append({
                        "rect": ui.Rect(x, y, width + icon_padding, height), 
                        "line_count": max(1, line_count - 1),
                        "content_text": current_page_text,
                        "content_height": current_content_height
                    })
                    
                    # Reset the variables
                    total_text_height = current_line_height
                    current_page_text = [text]
                    current_content_height = self.padding[0] + self.padding[2]
                    line_count = 1
                  
        # Make sure the remainder of the content gets placed on the final page
        if len(current_page_text) > 0 or len(layout_pages) == 0:
            
            # If we are dealing with a single line going over to the only other page
            # Just remove the footer to make up for space
            if len(layout_pages) == 1 and line_count == 1:
                layout_pages[0]['line_count'] = layout_pages[0]['line_count'] + 1
                layout_pages[0]['content_text'].extend(current_page_text)
                layout_pages[0]['content_height'] += current_line_height
            else: 
                width = min( self.limit_width, max(self.width, total_text_width + self.padding[1] + self.padding[3]))
                content_height = total_text_height + self.padding[0] + self.padding[2]
                height = min(self.limit_height, max(self.height, content_height))
                x = self.x if horizontal_alignment == "left" else self.limit_x + self.limit_width - width
                if horizontal_alignment == "center":
                    x = self.limit_x + ( self.limit_width - width ) / 2                
                y = self.limit_y if vertical_alignment == "top" else self.limit_y + self.limit_height - height
                
                layout_pages.append({
                    "rect": ui.Rect(x, y, width + icon_padding, height), 
                    "line_count": max(1, line_count + 2 ),
                    "content_text": current_page_text,
                    "content_height": content_height
                })
                
        return layout_pages
    
    def draw_content(self, canvas, paint, dimensions) -> bool:
        # Disable if there is no content
        if len(self.panel_content.content[0]) == 0:
            self.disable(True)
            return False

        paint.textsize = self.font_size
        
        paint.style = paint.Style.FILL
        
        # Draw the background first
        background_colour = self.theme.get_colour('text_box_background', 'F5F5F5')
        paint.color = background_colour
        
        # Animate the transition if an animation state has been set
        self.transition_animation_state = max(0, self.transition_animation_state - 1)
        if self.transition_animation_state > 0:
            growth = (self.max_transition_animation_state - self.transition_animation_state ) / self.max_transition_animation_state
            easeInOutQuint = 16 * growth ** 5 if growth < 0.5 else 1 - pow(-2 * growth + 2, 5) / 2
                        
            difference = ui.Rect((dimensions["rect"].x - self.previous_content_dimensions.x ) * easeInOutQuint,
                ( dimensions["rect"].y - self.previous_content_dimensions.y ) * easeInOutQuint,
                ( dimensions["rect"].width - self.previous_content_dimensions.width ) * easeInOutQuint,
                ( dimensions["rect"].height - self.previous_content_dimensions.height ) * easeInOutQuint)
                
            background_rect = ui.Rect(self.previous_content_dimensions.x + difference.x, 
                self.previous_content_dimensions.y + difference.y, 
                self.previous_content_dimensions.width + difference.width, 
                self.previous_content_dimensions.height + difference.height)
        
            self.draw_background(canvas, paint, background_rect)
        else:
            self.animated_word_state = max(0, self.animated_word_state - 1)
            self.draw_background(canvas, paint, dimensions["rect"])
                    
            paint.color = self.theme.get_colour('text_colour')
            self.draw_voice_command_backgrounds(canvas, paint, dimensions, self.animated_word_state)
            self.draw_content_text(canvas, paint, dimensions)
            self.draw_header_buttons(canvas, paint, dimensions)
            
        
        return self.transition_animation_state > 0 or self.animated_word_state > 0

    def draw_animation(self, canvas, animation_tick):
        if self.enabled and len(self.panel_content.content[0]) > 0:
            paint = canvas.paint
            if self.mark_layout_invalid and animation_tick == self.animation_max_duration - 1:
                self.layout = self.layout_content(canvas, paint)
                if self.page_index > len(self.layout) - 1:
                    self.page_index = len(self.layout) -1
            
            dimensions = self.layout[self.page_index]['rect']
            
            # Determine colour of the animation
            animation_progress = ( animation_tick - self.animation_max_duration ) / self.animation_max_duration
            red = self.intro_animation_start_colour[0] - int( self.blink_difference[0] * animation_progress )
            green = self.intro_animation_start_colour[1] - int( self.blink_difference[1] * animation_progress )
            blue = self.intro_animation_start_colour[2] - int( self.blink_difference[2] * animation_progress )
            red_hex = '0' + format(red, 'x') if red <= 15 else format(red, 'x')
            green_hex = '0' + format(green, 'x') if green <= 15 else format(green, 'x')
            blue_hex = '0' + format(blue, 'x') if blue <= 15 else format(blue, 'x')
            paint.color = red_hex + green_hex + blue_hex
            
            horizontal_alignment = "right" if self.limit_x < self.x else "left"
            if self.alignment == "center" or \
                ( self.x + self.width < self.limit_x + self.limit_width and self.limit_x < self.x ):
                horizontal_alignment = "center"
            
            growth = (self.animation_max_duration - animation_tick ) / self.animation_max_duration
            easeInOutQuint = 16 * growth ** 5 if growth < 0.5 else 1 - pow(-2 * growth + 2, 5) / 2
            
            width = dimensions.width * easeInOutQuint            
            if horizontal_alignment == "left":
                x = dimensions.x
            elif horizontal_alignment == "right":
                x = self.limit_x + self.limit_width - width
            elif horizontal_alignment == "center":
                x = self.limit_x + ( self.limit_width / 2 ) - ( width / 2 )
            
            rect = ui.Rect(x, dimensions.y, width, dimensions.height)
            self.draw_background(canvas, paint, rect)
            
            if animation_tick == 1:
                return self.draw(canvas)
            return True
        else:
            return False

    def draw_content_text(self, canvas, paint, dimensions) -> int:
        """Draws the content and returns the height of the drawn content"""
        paint.textsize = self.font_size
        
        # Line padding needs to accumulate to at least 1.5 times the font size
        # In order to make it readable according to WCAG specifications
        # https://www.w3.org/TR/WCAG21/#visual-presentation
        self.line_padding = int(self.font_size / 2) + 1 if self.font_size <= 17 else 5        
        
        rich_text = dimensions["content_text"]
        content_height = dimensions["content_height"]
        line_count = dimensions["line_count"]
        dimensions = dimensions["rect"]
       
        text_x = dimensions.x + self.padding[3]
        text_y = dimensions.y + self.padding[0] / 2
        
        self.draw_rich_text(canvas, paint, rich_text, text_x, text_y, self.line_padding)

    def draw_background(self, canvas, paint, rect):
        radius = 10
        rrect = skia.RoundRect.from_rect(rect, x=radius, y=radius)
        canvas.draw_rrect(rrect)
        
    def draw_voice_command_backgrounds(self, canvas, paint, dimensions, animation_state):
        text_colour = paint.color    
        rich_text = dimensions["content_text"]
        content_height = dimensions["content_height"]
        line_count = dimensions["line_count"]
        dimensions = dimensions["rect"]
        x = dimensions.x + self.padding[3]
        y = dimensions.y + self.padding[0]
            
        non_spoken_background_colour = self.theme.get_colour('voice_command_background_colour', '535353')
        spoken_background_colour = self.theme.get_colour('spoken_voice_command_background_colour', '6CC653')
    
        current_line = -1
        for index, text in enumerate(rich_text):
            current_line = current_line + 1 if text.x == 0 else current_line
            
            if text.x == 0:
                y += paint.textsize
                if index != 0:
                    y += self.line_padding
            
            if "command_available" in text.styles and text.text.strip() != "":
                command_padding = self.line_padding / 2
                
                text_size = max(paint.textsize, text.height)
                rect = ui.Rect(x + text.x - command_padding, y - paint.textsize - self.line_padding, 
                    text.width + command_padding * 2, text_size + command_padding * 2)
                
                if animation_state > 0 and str(index) in self.commands_positions and \
                    self.commands_positions[str(index)] in self.animated_words:
                    growth = (self.max_animated_word_state - animation_state ) / self.max_animated_word_state
                    easeOutQuad = 1 - pow(1 - growth, 4)                    
                    easeOutQuint = 1 - pow(1 - growth, 5)
                    
                    # Draw the colour shifting rectangle
                    colour_from = hex_to_ints(non_spoken_background_colour)
                    colour_to = hex_to_ints(spoken_background_colour)
                    red_value = int(round(colour_from[0] + ( colour_to[0] - colour_from[0] ) * easeOutQuad))
                    green_value = int(round(colour_from[1] + ( colour_to[1] - colour_from[1] ) * easeOutQuad))
                    blue_value = int(round(colour_from[2] + ( colour_to[2] - colour_from[2] ) * easeOutQuad))
                    red_hex = '0' + format(red_value, 'x') if red_value <= 15 else format(red_value, 'x')
                    green_hex = '0' + format(green_value, 'x') if green_value <= 15 else format(green_value, 'x')
                    blue_hex = '0' + format(blue_value, 'x') if blue_value <= 15 else format(blue_value, 'x')
                    colour = red_hex + green_hex + blue_hex
                    paint.color = colour
                    paint.style = Paint.Style.FILL
                    canvas.draw_rrect(skia.RoundRect.from_rect(rect, x=5, y=5))
                    
                    # Draw the expanding border
                    expand = ( self.font_size / 2 ) * easeOutQuint
                    alpha = int(round( 255 * (1 - easeOutQuint) ))
                    alpha_hex = '0' + format(alpha, 'x') if alpha <= 15 else format(alpha, 'x')
                    paint.color = colour + alpha_hex
                    paint.style = Paint.Style.STROKE
                    paint.stroke_width = 4
                    rect.x -= int(round(expand / 2))
                    rect.y -= int(round(expand / 2))
                    rect.height += int(round(expand))
                    rect.width += int(round(expand))
                    canvas.draw_rrect(skia.RoundRect.from_rect(rect, x=5, y=5))
                    paint.stroke_width = 1                    
                    
                # Not an animated set of words - Just draw the state
                else:
                    paint.color = spoken_background_colour if str(index) in self.commands_positions and \
                        self.commands_positions[str(index)] in self.content['walkthrough_said_voice_commands'] else non_spoken_background_colour
                    paint.style = Paint.Style.FILL
                    canvas.draw_rrect(skia.RoundRect.from_rect(rect, x=5, y=5))
        
        paint.color = text_colour
        paint.style = Paint.Style.FILL
        
        
    def draw_rich_text(self, canvas, paint, rich_text, x, y, line_padding, single_line=False):
        # Mostly copied over from layout_widget
        # Draw text line by line
        text_colour = paint.color
        error_colour = self.theme.get_colour('error_colour', 'AA0000')
        warning_colour = self.theme.get_colour('warning_colour', 'F75B00')
        success_colour = self.theme.get_colour('success_colour', '00CC00')
        info_colour = self.theme.get_colour('info_colour', '30AD9E')
    
        current_line = -1
        for index, text in enumerate(rich_text):
            paint.font.embolden = "bold" in text.styles
            paint.font.skew_x = -0.33 if "italic" in text.styles else 0
            paint.color = text_colour
            if "command_available" in text.styles:
                paint.color = self.theme.get_colour('spoken_voice_command_text_colour', '000000FF') \
                    if str(index) in self.commands_positions and self.commands_positions[str(index)] in self.content["walkthrough_said_voice_commands"] else \
                    self.theme.get_colour('voice_command_text_colour', 'DDDDDD')
            else:
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

    def draw_header_buttons(self, canvas, paint, dimensions):
        dimensions = dimensions["rect"]    
        # Header button tray 
        x = dimensions.x
        for index, icon in enumerate(self.icons):
            icon_position = Point2d(x + dimensions.width - (icon_radius * 1.5 + ( index * icon_radius * 2.2 )),
                dimensions.y + icon_radius + self.padding[0] / 2)
            self.icons[index].pos = icon_position
            paint.style = paint.Style.FILL
            if icon.id == "close":
                close_colour = self.theme.get_colour('close_icon_hover_colour') if self.icon_hovered == index else self.theme.get_colour('close_icon_accent_colour')            
                paint.shader = linear_gradient(icon_position.x, dimensions.y, icon_position.x, icon_position.y + icon_radius, (self.theme.get_colour('close_icon_colour'), close_colour))
                canvas.draw_circle(icon_position.x, icon_position.y, icon_radius, paint)