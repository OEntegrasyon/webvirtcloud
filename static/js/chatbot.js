question_input = document.getElementById("question");
button = document.getElementById("send-question");

const csrfToken = getCSRFToken();

function postData(url, dataform){
  return $.ajax({
    url: url,
    type: 'POST',
    data: JSON.stringify(dataform),
    contentType: 'application/json',
    headers: {
      'X-CSRFToken': csrfToken
    },
    success: function (response) {},
    error: function (jqXHR, textStatus, errorThrown) {}
  });
}

function getCSRFToken() {
  let cookieValue = null;
  const cookies = document.cookie.split(';');
  for (let i = 0; i < cookies.length; i++) {
    const cookie = cookies[i].trim();
    if (cookie.startsWith('csrftoken=')) {
      cookieValue = cookie.substring('csrftoken='.length);
      break;
    }
  }
  return cookieValue;
}

button.onclick = function() {
    question = question_input.value;
    postData('/chatbot/get-response/', {"question": question})
    .done(function(response) {
        console.log(response);
    });
}