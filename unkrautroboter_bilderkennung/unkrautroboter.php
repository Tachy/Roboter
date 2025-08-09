<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MJPEG Stream</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            background-color: #f0f0f0;
        }
        #video-container {
            background-color: black;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
        }
        img {
            width: 1280px;  /* Anpassung der Bildgrö�~_e */
            height: 720px;
        }
    </style>
</head>
<body>
    <div id="video-container">
        <h2>Live MJPEG Stream</h2>
        <img src="http://192.168.179.252:8080/stream" alt="MJPEG Stream">
    </div>
</body>
</html>