var React = require('react');
var Renderer = require('@react-pdf/renderer');

function buildTemplate(strings, values) {
  if (typeof strings === 'string') return strings;

  var output = '';
  var i;
  for (i = 0; i < strings.length; i += 1) {
    output += strings[i] || '';
    if (i < values.length) {
      output += values[i] == null ? '' : String(values[i]);
    }
  }
  return output;
}

function toCamelCase(input) {
  return input.trim().replace(/-([a-z])/g, function (_match, character) {
    return character.toUpperCase();
  });
}

function normalizeValue(raw) {
  var value = raw.trim();
  if (/^-?\d+(\.\d+)?pt$/i.test(value)) return Number(value.slice(0, -2));
  if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
  return value;
}

function parseCss(cssText) {
  var style = {};
  var declarations = cssText.split(';');
  var i;

  for (i = 0; i < declarations.length; i += 1) {
    var trimmed = declarations[i].trim();
    if (!trimmed) continue;

    var separator = trimmed.indexOf(':');
    if (separator === -1) continue;

    var property = toCamelCase(trimmed.slice(0, separator));
    var value = normalizeValue(trimmed.slice(separator + 1));
    style[property] = value;
  }

  return style;
}

function createFactory(baseComponent, inheritedStyles, displayName) {
  inheritedStyles = inheritedStyles || [];
  displayName = displayName || '';

  return function styledTag(strings) {
    var values = Array.prototype.slice.call(arguments, 1);
    var nextStyle = parseCss(buildTemplate(strings, values));
    var accumulatedStyles = inheritedStyles.concat(nextStyle);

    function StyledComponent(props) {
      var finalProps = {};
      var key;

      for (key in props) {
        if (Object.prototype.hasOwnProperty.call(props, key)) {
          finalProps[key] = props[key];
        }
      }

      finalProps.style = props && props.style
        ? accumulatedStyles.concat(props.style)
        : accumulatedStyles;

      if (finalProps.style.length === 1) {
        finalProps.style = finalProps.style[0];
      }

      return React.createElement(baseComponent, finalProps);
    }

    StyledComponent.displayName = displayName
      ? 'Styled(' + displayName + ')'
      : 'StyledComponent';
    StyledComponent.__styledBaseComponent = baseComponent;
    StyledComponent.__styledStyles = accumulatedStyles;
    return StyledComponent;
  };
}

function styled(target) {
  var baseComponent = target.__styledBaseComponent || target;
  var inheritedStyles = target.__styledStyles || [];
  var displayName =
    target.displayName || target.name || String(baseComponent || target);

  return createFactory(baseComponent, inheritedStyles, displayName);
}

['Document', 'Page', 'Text', 'View', 'Image', 'Link', 'Note'].forEach(function (key) {
  if (Renderer[key]) {
    styled[key] = createFactory(Renderer[key], [], key);
  }
});

module.exports = styled;
module.exports.default = styled;
